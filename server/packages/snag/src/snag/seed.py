"""Seed the six authored example projects (project-3-spec.md §11/§14) by
running each one through the REAL pipeline end to end: extract -> surfaces
-> resolve any follow-up questions extraction raised -> scan (attacks +
gap probes, both inside `snag.runner.run_scan`) -> propose fixes. Every
step calls the same production module a live request would
(`snag.extract`, `snag.surfaces`, `snag.followups`, `snag.runner`,
`snag.fixes`) — nothing here re-implements pipeline logic, it only drives
it once per authored prompt and stores the result at a FIXED slug.

`seed_examples(db, completions)` is deliberately generic over `completions`
(`substrate.llm.Completions`): the `snag seed` CLI passes a real,
owner-key-funded adapter (real spend, real models — T-15-02's "seeding
makes real calls once at build/deploy time"); `tests/test_seed_corpus.py`
passes a scripted double so the same code path is exercised
deterministically with no network call. Neither caller needs its own copy
of this orchestration.

Idempotent by slug (T-15-02): if a project already exists at an example's
fixed slug, seeding it is a no-op — no re-extraction, no re-scan, no
re-spend. A redeploy that calls `snag seed` again costs nothing once the
six are seeded.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from snag.api.deps import validate_model
from snag.api.routers.questions import ROUND_CAP
from snag.extract import ExtractedRule, extract_rules
from snag.fixes import ensure_fixes_proposed
from snag.followups import normalize_answer
from snag.runner import DEFAULT_CALL_CAP, DEFAULT_SPEND_CAP, ScanStartConfig, run_scan, start_scan
from snag.seed_prompts import SEED_PROMPTS, SeedPromptSpec
from snag.surfaces import build_surface_map
from substrate.db import Database
from substrate.llm import Completions

log = structlog.get_logger(__name__)


def _answer_for(spec: SeedPromptSpec, rule: ExtractedRule) -> str:
    """The pre-authored answer for an open question `rule` raised, matched
    by a substring of the rule's own extracted `text` (case-insensitive) —
    a real extraction pass may paraphrase a rule differently than a
    scripted test fixture does, so falling back to `spec.fallback_answer`
    ("you pick") for anything unmatched is the graceful path, not a bug:
    `normalize_answer` resolves that to a best-guess `status="inferred"`
    rather than leaving the rule untestable forever."""
    lowered = rule.text.lower()
    for needle, answer in spec.answers.items():
        if needle.lower() in lowered:
            return answer
    return spec.fallback_answer


def _config_override_for(spec: SeedPromptSpec, rule_text: str) -> dict[str, Any]:
    """The merged `spec.config_overrides` entries whose needle matches
    `rule_text`, matched the same way `_answer_for` matches `answers`.
    Empty when nothing matches — the common case, a rule whose
    checker_config the extractor already got right."""
    lowered = rule_text.lower()
    merged: dict[str, Any] = {}
    for needle, override in spec.config_overrides.items():
        if needle.lower() in lowered:
            merged.update(override)
    return merged


async def _apply_final_overrides(
    db: Database, spec: SeedPromptSpec, rule_ids: list[int], rules: list[ExtractedRule]
) -> None:
    """Applied AFTER follow-ups resolve (not at insert time): an override
    must win over whatever a follow-up round inferred, since it exists
    specifically to correct a checker_config the extractor (and,
    transitively, the follow-up round answering its own open questions)
    got wrong for a checker type with no worked example to imitate. Uses
    the same jsonb merge as `_resolve_follow_ups` — an override only
    touches the keys it names, leaving the rest of the rule's
    checker_config (whatever the model got right) alone."""
    for rule_id, rule in zip(rule_ids, rules, strict=True):
        override = _config_override_for(spec, rule.text)
        if not override:
            continue
        async with db.acquire() as conn:
            await conn.execute(
                """UPDATE rules SET checker_config = COALESCE(checker_config, '{}'::jsonb) || $1
                   WHERE id = $2""",
                override,
                rule_id,
            )


async def _insert_project(
    conn: Any, spec: SeedPromptSpec, rules: list[ExtractedRule]
) -> tuple[list[int], int]:
    await conn.execute(
        """INSERT INTO projects (id, name, model, tools_json, ephemeral, seeded)
           VALUES ($1, $2, $3, $4, false, true)""",
        spec.slug,
        spec.title,
        spec.model,
        spec.tools_json,
    )
    prompt_version_id = await conn.fetchval(
        """INSERT INTO prompt_versions (project_id, full_text, tools_json)
           VALUES ($1, $2, $3) RETURNING id""",
        spec.slug,
        spec.system_prompt,
        spec.tools_json,
    )
    rule_ids: list[int] = []
    for rule in rules:
        rule_id = await conn.fetchval(
            """INSERT INTO rules
                   (project_id, text, category, direction, source_line,
                    checker_type, checker_config, testable, confidence, in_prompt)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true)
               RETURNING id""",
            spec.slug,
            rule.text,
            rule.category,
            rule.direction,
            rule.source_line,
            rule.checker_type,
            rule.checker_config,
            rule.checker_type != "none",
            rule.confidence,
        )
        rule_ids.append(int(rule_id))
    return rule_ids, int(prompt_version_id)


async def _insert_surfaces(db: Database, spec: SeedPromptSpec) -> None:
    """§5's surface map, generated the same pure way `POST .../surfaces`
    does — but inserted already `confirmed = true`. A seeded example has no
    human to click through the Confirm step (§2 Step 5); every surface it
    ships with is one the seed author has already reviewed."""
    surface_specs = build_surface_map(spec.system_prompt, spec.tools_json)
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM surfaces WHERE project_id = $1", spec.slug)
        for s in surface_specs:
            await conn.execute(
                """INSERT INTO surfaces
                       (project_id, kind, path, source, risk, user_controlled, note, tests, confirmed)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, true)""",
                spec.slug,
                s.kind,
                s.path,
                s.source,
                s.risk,
                s.user_controlled,
                s.note,
                s.tests,
            )


async def _resolve_follow_ups(
    db: Database, completions: Completions, spec: SeedPromptSpec, rule_id: int, rule: ExtractedRule
) -> None:
    """FOLLOWUP-01/02/03, driven the same way `POST .../questions/answers`
    is: insert round-1 `questions` rows for every open question extraction
    raised on this rule, answer each with a pre-authored (or best-guess
    fallback) answer, apply the normalized result to the rule, and follow
    any NEW question `normalize_answer` itself raises up to `ROUND_CAP`
    rounds (T-08-03) — exactly the cap the production round-answering
    endpoint enforces."""
    open_questions = list(rule.open_questions)
    current_round = 1
    while open_questions and current_round <= ROUND_CAP:
        next_round_questions: list[str] = []
        for question_text in open_questions:
            async with db.acquire() as conn:
                question_id = await conn.fetchval(
                    """INSERT INTO questions (rule_id, project_id, round, text, status)
                       VALUES ($1, $2, $3, $4, 'open') RETURNING id""",
                    rule_id,
                    spec.slug,
                    current_round,
                    question_text,
                )
            answer_raw = _answer_for(spec, rule)
            normalized = await normalize_answer(
                completions,
                question=question_text,
                answer_raw=answer_raw,
                system=spec.system_prompt,
                model=spec.model,
                run_id=f"seed:{spec.slug}:followup:{question_id}",
            )
            async with db.acquire() as conn, conn.transaction():
                await conn.execute(
                    """UPDATE questions
                           SET answer_raw = $1, answer_normalized = $2, status = $3, conflict_note = $4
                       WHERE id = $5""",
                    answer_raw,
                    json.dumps(normalized.checker_config),
                    normalized.status,
                    normalized.conflict_note,
                    question_id,
                )
                if normalized.status in ("answered", "inferred"):
                    # MERGE, never overwrite (mirrors questions.py's own
                    # fix): a rule commonly raises more than one open
                    # question, and a plain overwrite would let this
                    # question's answer erase an earlier one's contribution
                    # to the same rule's checker_config.
                    await conn.execute(
                        """UPDATE rules SET checker_config = COALESCE(checker_config, '{}'::jsonb) || $1
                           WHERE id = $2""",
                        normalized.checker_config,
                        rule_id,
                    )
                elif normalized.status == "skipped":
                    await conn.execute(
                        "UPDATE rules SET testable = false WHERE id = $1", rule_id
                    )
            if normalized.status in ("answered", "inferred"):
                next_round_questions.extend(normalized.follow_up_questions)
        open_questions = next_round_questions
        current_round += 1


async def _project_exists(db: Database, slug: str) -> bool:
    async with db.acquire() as conn:
        return bool(await conn.fetchval("SELECT 1 FROM projects WHERE id = $1", slug))


async def _seed_one(db: Database, completions: Completions, spec: SeedPromptSpec) -> bool:
    """Run the full pipeline for one authored prompt. Returns `False`
    (no-op) when `spec.slug` already exists — idempotency (T-15-02)."""
    if await _project_exists(db, spec.slug):
        log.info("seed.already_seeded", slug=spec.slug)
        return False

    validate_model(spec.model)

    tools_text = json.dumps(spec.tools_json) if spec.tools_json else None
    extraction = await extract_rules(
        completions,
        model=spec.model,
        system=spec.system_prompt,
        tools=tools_text,
        run_id=f"seed:{spec.slug}:extract",
    )
    if extraction.malformed:
        log.warning("seed.extraction_malformed", slug=spec.slug)

    async with db.acquire() as conn, conn.transaction():
        rule_ids, prompt_version_id = await _insert_project(conn, spec, extraction.rules)

    await _insert_surfaces(db, spec)

    for rule_id, rule in zip(rule_ids, extraction.rules, strict=True):
        if rule.open_questions:
            await _resolve_follow_ups(db, completions, spec, rule_id, rule)

    await _apply_final_overrides(db, spec, rule_ids, extraction.rules)

    scan_id = await start_scan(
        db,
        slug=spec.slug,
        config=ScanStartConfig(
            mode="custom",
            surfaces=list(spec.surfaces),
            repeats=spec.repeats,
            call_cap=spec.call_cap or DEFAULT_CALL_CAP,
            spend_cap=spec.spend_cap or DEFAULT_SPEND_CAP,
        ),
        model=spec.model,
        prompt_version_id=prompt_version_id,
    )
    await run_scan(db, scan_id, completions=completions)

    await ensure_fixes_proposed(db, spec.slug, completions=completions)

    log.info("seed.seeded", slug=spec.slug, rules=len(rule_ids), scan_id=scan_id)
    return True


async def seed_examples(db: Database, completions: Completions) -> list[str]:
    """Seed every example in `SEED_PROMPTS`. Returns the slugs actually
    seeded this call (already-seeded slugs are skipped and excluded)."""
    seeded: list[str] = []
    for spec in SEED_PROMPTS:
        if await _seed_one(db, completions, spec):
            seeded.append(spec.slug)
    return seeded
