"""POST /api/projects (Paste): a pasted system prompt becomes a project, an
extracted rule set, and the one always-present "chat" surface. GET returns
the bare project row.

This module also owns the rules-editing surface (EXTRACT-03: add / edit /
delete / toggle-testable, all against `app.state.db` with parameterized SQL)
and the two privacy primitives the paste screen promises (PRIV-01: permanent
cascading delete; PRIV-02: ephemeral projects never get a durable copy of
the pasted prompt).
"""

from __future__ import annotations

import json
import secrets
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from snag.api.app import ctx
from snag.api.deps import get_completions, require_slug, validate_model
from snag.config import get_settings
from snag.extract import extract_rules
from substrate.llm import Completions

log = structlog.get_logger(__name__)
router = APIRouter()

# T-01-03/T-06-04: length caps enforced before any model call or DB write.
# Pydantic's max_length rejects an oversized body with 422 during request
# parsing — before any handler body runs, and therefore before extract_rules
# or an INSERT/UPDATE ever executes.
MAX_SYSTEM_PROMPT_CHARS = 20_000
MAX_TOOLS_CHARS = 50_000
MAX_RULE_TEXT_CHARS = 2_000
MAX_CHECKER_CONFIG_CHARS = 5_000

#: T-06-03: the exhaustive, hand-maintained list of `rules` columns a PATCH
#: may ever touch. Built from the Pydantic field names declared on
#: `RulePatchRequest` (itself `extra="forbid"`) rather than from the request
#: body's own keys, so there is no path from an attacker-controlled dict to
#: an arbitrary column name reaching the UPDATE below.
_PATCHABLE_RULE_FIELDS = (
    "text",
    "category",
    "direction",
    "checker_type",
    "checker_config",
    "testable",
    "confidence",
)


def _validate_checker_config_size(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is not None and len(json.dumps(value)) > MAX_CHECKER_CONFIG_CHARS:
        msg = f"checker_config must serialize to at most {MAX_CHECKER_CONFIG_CHARS} chars"
        raise ValueError(msg)
    return value


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(min_length=1, max_length=MAX_SYSTEM_PROMPT_CHARS)
    tools: str | None = Field(default=None, max_length=MAX_TOOLS_CHARS)
    model: str | None = None
    ephemeral: bool = False
    """PRIV-02: when true, neither the system prompt nor the tool
    definitions are ever written to `prompt_versions` (or `projects.
    tools_json`) — see `create_project`'s ephemeral branch below."""


class CreateProjectResponse(BaseModel):
    slug: str


class RuleCreateRequest(BaseModel):
    """EXTRACT-03: a user-typed rule. Always persisted with `in_prompt =
    false` and `confirmed_by_user = true` — a rule the user typed in was
    never in the pasted prompt by definition, and is confirmed the moment
    they add it (there is nothing left to confirm)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_RULE_TEXT_CHARS)
    category: str = "other"
    direction: str = "negative"
    checker_type: str = "none"
    checker_config: dict[str, Any] | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    _check_checker_config_size = field_validator("checker_config")(_validate_checker_config_size)


class RulePatchRequest(BaseModel):
    """EXTRACT-03 + T-06-03: every field here is optional (partial update),
    but the fixed field set IS the allow-list — `model_dump(exclude_unset=
    True)` in `update_rule` can only ever produce keys from this list, no
    matter what extra keys the request body contained (rejected by
    `extra="forbid"` with a 422 before the handler runs)."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=MAX_RULE_TEXT_CHARS)
    category: str | None = None
    direction: str | None = None
    checker_type: str | None = None
    checker_config: dict[str, Any] | None = None
    testable: bool | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    _check_checker_config_size = field_validator("checker_config")(_validate_checker_config_size)


def _rule_row_to_ui(row: Any) -> dict[str, Any]:
    """DB row (+ the `attacks`/`breaks` columns every query below joins in)
    -> the UI's Rule shape (src/data/types.ts). `inPrompt` is Snag's own
    addition beyond that shape: it is what lets the report distinguish a
    user-typed rule from one the extractor actually found (EXTRACT-03)."""
    entry: dict[str, Any] = {
        "id": str(row["id"]),
        "text": row["text"],
        "category": row["category"],
        "direction": row["direction"],
        "sourceLine": row["source_line"] or "",
        "checkerType": row["checker_type"],
        "checkerConfig": row["checker_config"] or {},
        "testable": row["testable"],
        "confidence": float(row["confidence"] or 0.0),
        "attacks": row["attacks"],
        "breaks": row["breaks"],
        "inPrompt": row["in_prompt"],
    }
    if not row["testable"]:
        entry["untestableReason"] = (
            "You added this rule; Snag tests the behaviour but it wasn't found in your prompt."
            if not row["in_prompt"]
            else "Snag's extractor could not derive a mechanical checker for this rule."
        )
    return entry


# Every rules query below joins the same attack-run tally so a rule fetched
# right after creation and one fetched after a scan carry the same shape —
# no separate "before any scan" branch to keep in sync.
_RULE_SELECT = """\
    SELECT r.*,
           COALESCE(a.attacks, 0) AS attacks,
           COALESCE(a.breaks, 0) AS breaks
    FROM rules r
    LEFT JOIN (
        SELECT rule_id, COUNT(*) AS attacks, COUNT(*) FILTER (WHERE NOT passed) AS breaks
        FROM attack_runs
        GROUP BY rule_id
    ) a ON a.rule_id = r.id
"""


async def _fetch_rule(conn: Any, slug: str, rule_id: int) -> Any:
    return await conn.fetchrow(
        f"{_RULE_SELECT} WHERE r.project_id = $1 AND r.id = $2", slug, rule_id
    )


@router.post("/projects", response_model=CreateProjectResponse)
async def create_project(
    body: CreateProjectRequest,
    request: Request,
    completions: Completions = Depends(get_completions),  # noqa: B008 - FastAPI DI idiom
) -> CreateProjectResponse:
    settings = get_settings()
    model = body.model or settings.default_model
    validate_model(model)  # KEY-03: before any completions/model call

    tools_obj = None
    if body.tools:
        try:
            tools_obj = json.loads(body.tools)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="tools must be valid JSON") from exc

    extraction = await extract_rules(
        completions, model=model, system=body.system_prompt, tools=body.tools
    )
    if extraction.malformed:
        # EXTRACT-02/EXTRACT-03: a shaky extraction pass degrades to "zero
        # rules found" rather than a 500 — the user's type-your-own-rules
        # safety net (POST .../rules below) is what makes this survivable.
        log.warning("project.extraction_malformed", model=model)

    # Unguessable by construction (T-01-05) — 9 bytes -> 12 URL-safe base64
    # chars, never interpolated into SQL (T-01-04: bound as a parameter below).
    slug = secrets.token_urlsafe(9)
    state = ctx(request)

    # PRIV-02: an ephemeral project never gets a durable copy of the pasted
    # prompt or tool definitions. `tools_json` is withheld from the
    # `projects` row and the `prompt_versions` row is skipped entirely — the
    # single largest, most sensitive artifact (the proprietary system
    # prompt) never touches disk for these projects. Rules/surfaces still
    # persist so the Confirm step (§2 Step 5) keeps working across requests;
    # DELETE /projects/{slug} (PRIV-01) is what a client calls once the
    # report has been shown, clearing everything that remains.
    stored_tools_obj = None if body.ephemeral else tools_obj

    async with state.db.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO projects (id, model, tools_json, ephemeral) VALUES ($1, $2, $3, $4)",
            slug,
            model,
            stored_tools_obj,
            body.ephemeral,
        )
        if not body.ephemeral:
            await conn.execute(
                """INSERT INTO prompt_versions (project_id, full_text, tools_json)
                   VALUES ($1, $2, $3)""",
                slug,
                body.system_prompt,
                tools_obj,
            )
        for rule in extraction.rules:
            await conn.execute(
                """INSERT INTO rules
                       (project_id, text, category, direction, source_line,
                        checker_type, checker_config, testable, confidence)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                slug,
                rule.text,
                rule.category,
                rule.direction,
                rule.source_line,
                rule.checker_type,
                rule.checker_config,
                rule.checker_type != "none",
                rule.confidence,
            )
        # The chat surface always exists — every pasted prompt reaches the
        # model through a user message. Detecting template-var / tool-param /
        # tool-return surfaces from `tools_obj` is 01-01's follow-on
        # (surfaces.py, architecture-plan.md build-order step 4).
        await conn.execute(
            """INSERT INTO surfaces (project_id, kind, path, source, user_controlled)
               VALUES ($1, 'chat', 'user message', 'chat input', true)""",
            slug,
        )

    log.info("project.created", slug=slug, rules=len(extraction.rules), ephemeral=body.ephemeral)
    return CreateProjectResponse(slug=slug)


@router.get("/projects/{slug}")
async def get_project(slug: str, request: Request) -> dict[str, object]:
    row = await require_slug(request, slug)
    return {
        "slug": row["id"],
        "name": row["name"],
        "model": row["model"],
        "ephemeral": row["ephemeral"],
        "seeded": row["seeded"],
        "createdAt": row["created_at"].isoformat(),
    }


@router.delete("/projects/{slug}", status_code=204)
async def delete_project(slug: str, request: Request) -> Response:
    """PRIV-01: one parameterized DELETE, relying entirely on the `ON DELETE
    CASCADE` foreign keys the schema migration already declares — every
    prompt version, rule, question, surface, scan, attack_run, gap, and fix
    under this project id goes with it. No per-table cleanup to keep in sync
    (and forget) as new child tables are added."""
    await require_slug(request, slug)
    state = ctx(request)
    async with state.db.acquire() as conn:
        await conn.execute("DELETE FROM projects WHERE id = $1", slug)
    log.info("project.deleted", slug=slug)
    return Response(status_code=204)


@router.get("/projects/{slug}/rules")
async def list_rules(slug: str, request: Request) -> list[dict[str, Any]]:
    await require_slug(request, slug)
    state = ctx(request)
    async with state.db.acquire() as conn:
        rows = await conn.fetch(f"{_RULE_SELECT} WHERE r.project_id = $1 ORDER BY r.id", slug)
    return [_rule_row_to_ui(row) for row in rows]


@router.post("/projects/{slug}/rules", status_code=201)
async def create_rule(slug: str, body: RuleCreateRequest, request: Request) -> dict[str, Any]:
    """EXTRACT-03: a rule the user typed in — always `in_prompt = false`
    (it wasn't in the pasted prompt by definition) and `confirmed_by_user =
    true` (adding it IS confirming it)."""
    await require_slug(request, slug)
    state = ctx(request)
    async with state.db.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """INSERT INTO rules
                   (project_id, text, category, direction, source_line,
                    checker_type, checker_config, testable, confidence,
                    confirmed_by_user, in_prompt)
               VALUES ($1, $2, $3, $4, '', $5, $6, $7, $8, true, false)
               RETURNING id""",
            slug,
            body.text,
            body.category,
            body.direction,
            body.checker_type,
            body.checker_config or {},
            body.checker_type != "none",
            body.confidence,
        )
        rule = await _fetch_rule(conn, slug, row["id"])
    log.info("rule.created", slug=slug, rule_id=row["id"])
    return _rule_row_to_ui(rule)


@router.patch("/projects/{slug}/rules/{rule_id}")
async def update_rule(
    slug: str, rule_id: int, body: RulePatchRequest, request: Request
) -> dict[str, Any]:
    """EXTRACT-03: partial update of text/category/direction/checker_type/
    checker_config/testable/confidence. T-06-03: the only columns this can
    ever touch are `_PATCHABLE_RULE_FIELDS` — every key in
    `model_dump(exclude_unset=True)` comes from `RulePatchRequest`'s own
    (fixed, `extra="forbid"`) field set, so there is no way for a request
    body to name a column outside that list."""
    await require_slug(request, slug)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    assert set(updates) <= set(_PATCHABLE_RULE_FIELDS)  # defence in depth, not the gate

    set_clauses = []
    values: list[Any] = []
    for key, value in updates.items():
        values.append(value)
        set_clauses.append(f"{key} = ${len(values)}")
    values.append(slug)
    slug_param = len(values)
    values.append(rule_id)
    id_param = len(values)

    state = ctx(request)
    async with state.db.acquire() as conn, conn.transaction():
        # S608: the interpolated fragment is a fixed comma-joined list of
        # `key = $n` clauses whose keys come only from `_PATCHABLE_RULE_FIELDS`
        # (asserted above) — every value is still bound as a `$n` parameter,
        # never interpolated. No request-controlled string reaches this SQL.
        updated = await conn.fetchval(
            f"""UPDATE rules SET {", ".join(set_clauses)}
                WHERE project_id = ${slug_param} AND id = ${id_param}
                RETURNING id""",  # noqa: S608
            *values,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="no such rule")
        rule = await _fetch_rule(conn, slug, rule_id)
    log.info("rule.updated", slug=slug, rule_id=rule_id, fields=list(updates))
    return _rule_row_to_ui(rule)


@router.delete("/projects/{slug}/rules/{rule_id}", status_code=204)
async def delete_rule(slug: str, rule_id: int, request: Request) -> Response:
    await require_slug(request, slug)
    state = ctx(request)
    async with state.db.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM rules WHERE project_id = $1 AND id = $2 RETURNING id", slug, rule_id
        )
    if deleted is None:
        raise HTTPException(status_code=404, detail="no such rule")
    log.info("rule.deleted", slug=slug, rule_id=rule_id)
    return Response(status_code=204)
