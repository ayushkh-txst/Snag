"""§7.1/§7.2 acceptance: family coverage, canary discipline, licence
provenance, and idempotent seeding (ATTACK-01)."""

from __future__ import annotations

from pathlib import Path

from snag.attacks.library import (
    FAMILIES,
    TECHNIQUES,
    needs_canary,
    profile_for_model,
    techniques_for_model,
)
from snag.attacks.seed_techniques import seed_techniques
from substrate.db import Database

LICENCES_PATH = Path(__file__).parent.parent / "src" / "snag" / "attacks" / "LICENCES.md"

# §7.1's own list plus the four app-specific families backend-feasibility.md
# calls out — the ones public sets mostly miss.
EXPECTED_FAMILIES = {
    "instruction_override",
    "roleplay",
    "encoding",
    "context_switch",
    "authority_claim",
    "translation",
    "debug_pretext",
    "continuation",
    "payload_splitting",
    "obfuscation",
    "many_shot",
    "business_logic_bypass",
    "tool_arg_injection",
    "auth_confusion",
    "refusal_bypass",
    # 2026 hardening pass (backend research report).
    "verbatim_extraction",
    "policy_puppetry",
    "template_forgery",
    "indirect_envelope",
    "tool_error_injection",
    "url_exfiltration",
    "refusal_suppression",
    "past_tense",
    "context_padding",
    "shallow_cipher",
    # phase 2 of the same pass: the families that needed runner support.
    "prefill",
    "escalation_ladder",
    # The control condition: ask plainly, with no manipulation at all.
    "direct_request",
}


def test_every_expected_family_has_at_least_one_technique() -> None:
    families = {t.family for t in TECHNIQUES}
    missing = EXPECTED_FAMILIES - families
    assert not missing, f"missing families: {missing}"
    # FAMILIES (the exported constant) and the data itself must agree.
    assert set(FAMILIES) == families


def test_no_technique_belongs_to_an_unexpected_family() -> None:
    families = {t.family for t in TECHNIQUES}
    assert families <= EXPECTED_FAMILIES


def test_canary_dependent_techniques_carry_a_canary() -> None:
    for technique in TECHNIQUES:
        if needs_canary(technique):
            assert technique.canary, (
                f"{technique.id} reaches tool_return or is a roleplay attack "
                "but has no canary"
            )


def test_techniques_without_the_canary_condition_are_not_required_to_have_one() -> None:
    """Sanity check on the fixture data itself: at least one technique
    exercises the "canary not required" path, so the assertion above isn't
    vacuously true."""
    assert any(not needs_canary(t) for t in TECHNIQUES)


def test_every_technique_has_licence_and_source() -> None:
    for technique in TECHNIQUES:
        assert technique.licence, f"{technique.id} has no licence"
        assert technique.source, f"{technique.id} has no source"


def test_technique_ids_are_unique() -> None:
    ids = [t.id for t in TECHNIQUES]
    assert len(ids) == len(set(ids))


def test_every_technique_targets_at_least_one_category_and_surface() -> None:
    for technique in TECHNIQUES:
        assert technique.targets, f"{technique.id} has no targets"
        assert technique.surfaces, f"{technique.id} has no surfaces"


def test_multi_turn_techniques_end_with_their_own_template() -> None:
    """`turns[-1]` is the final ask; it should be the same text as
    `template` so instantiation fills them identically."""
    for technique in TECHNIQUES:
        if technique.turns:
            assert technique.turns[-1] == technique.template


def test_licences_md_lists_every_source_with_a_commercial_use_verdict() -> None:
    text = LICENCES_PATH.read_text()
    sources = {t.source for t in TECHNIQUES}
    for source in sources:
        assert source in text, f"LICENCES.md doesn't mention source {source!r}"
    assert "commercial use" in text.lower()


def test_every_indirect_surface_technique_carries_a_canary() -> None:
    """gap §5.5: a canary checker PASSES when nothing was planted, so an
    indirect (tool_return) technique without a canary is a silent no-op.
    `needs_canary` must flag every one, and every flagged one must carry it."""
    for technique in TECHNIQUES:
        if "tool_return" in technique.surfaces:
            assert needs_canary(technique), f"{technique.id} reaches tool_return but isn't flagged"
            assert technique.canary, f"{technique.id} reaches tool_return with no canary"


def test_every_indirect_family_has_a_poisoned_cover_story_or_default() -> None:
    from snag.attacks.poisoned import poisoned_template

    for technique in TECHNIQUES:
        if needs_canary(technique):
            assert technique.canary is not None
            template = poisoned_template(technique.family)
            # Must be fillable with the technique's canary and yield it.
            assert "{canary}" in template
            assert technique.canary in template.format(canary=technique.canary)


def test_tier_c_encodings_are_gated_to_the_frontier_profile() -> None:
    """report TIER C: deep encodings fail HARMLESSLY on cheap models (false
    'held'), so they must not run on the small-model profile."""
    gated = {"encoding.01", "obfuscation.01", "roleplay.01"}
    by_id = {t.id: t for t in TECHNIQUES}
    for tid in gated:
        assert by_id[tid].profile == "frontier", f"{tid} should be frontier-gated"
    # The cheap-model-capable replacement is NOT gated.
    assert by_id["shallow_cipher.01"].profile == "all"


# ------------------------------------------------- phase 2: S1 / S2 / gating


def test_only_the_prefill_family_carries_a_prefill_and_every_member_has_one() -> None:
    """report §S1. The forged assistant turn is the family's whole mechanism,
    so a `prefill` technique without one would be an ordinary single-turn ask
    wearing the wrong name, and a non-`prefill` technique with one would forge
    a turn nothing documents."""
    for technique in TECHNIQUES:
        if technique.family == "prefill":
            assert technique.prefill, f"{technique.id} is a prefill technique with no prefill"
        else:
            assert technique.prefill is None, f"{technique.id} carries an undocumented prefill"


def test_no_prefill_ever_contains_its_own_canary() -> None:
    """The canary must be something the MODEL emitted. Planting it in the
    forged turn would let the attack satisfy its own checker — the exact
    false-positive `Transcript.assistant_text()` excludes forged turns to
    prevent, asserted here as well so the data can't drift into relying on
    that backstop."""
    for technique in TECHNIQUES:
        if technique.prefill and technique.canary:
            assert technique.canary not in technique.prefill


def test_prefill_techniques_never_reach_the_indirect_surface() -> None:
    """`runner._execute_indirect_attack` builds its own fixed exchange and
    never inserts a prefill, so a `tool_return` prefill technique would be a
    silent no-op — the forged turn would simply never be sent."""
    for technique in TECHNIQUES:
        if technique.prefill:
            assert "tool_return" not in technique.surfaces


def test_the_escalation_ladder_is_a_fixed_four_rung_script_ending_in_its_own_ask() -> None:
    """report §S2: the ladder is fixed at four rungs with only the slots
    varying, so generation stays deterministic. Rungs 2 and 3 must each refer
    back to the model's OWN prior answer — that back-reference is the whole
    mechanism (compliance accumulates, refusal precedent never does); a
    ladder of four independent questions would just be padding again."""
    ladders = [t for t in TECHNIQUES if t.family == "escalation_ladder"]
    assert ladders
    for ladder in ladders:
        assert len(ladder.turns) == 4
        assert ladder.turns[-1] == ladder.template
        assert "your own answer above" in ladder.turns[1]
        assert "you just described" in ladder.turns[2] or "that edge case" in ladder.turns[2]
        assert "already agreed to above" in ladder.turns[3]
        assert ladder.canary and "{canary}" in ladder.template


def test_profile_gating_drops_frontier_only_techniques_for_a_cheap_model() -> None:
    """report TIER C: a deep-encoding technique against a model that cannot
    decode fails for a reason unrelated to the rule, and Snag would record a
    false "held". Gating removes the run instead of scoring it."""
    small = techniques_for_model("qwen/qwen3.8-flash")
    frontier = techniques_for_model("openai/gpt-5.6-luna")

    assert profile_for_model("qwen/qwen3.8-flash") == "small"
    assert profile_for_model("deepseek/deepseek-v4-flash-0731") == "small"
    assert profile_for_model("openai/gpt-5.6-luna") == "frontier"

    small_ids = {t.id for t in small}
    frontier_ids = {t.id for t in frontier}
    for gated in ("encoding.01", "obfuscation.01", "roleplay.01"):
        assert gated not in small_ids, f"{gated} is frontier-only and must not run on a cheap model"
        assert gated in frontier_ids
    # ...and what a cheap target actually gets is the structural set the
    # report ranks first for it, not a thinned-out version of the same list.
    for kept in (
        "prefill.01",
        "prefill.02",
        "prefill.03",
        "escalation_ladder.01",
        "template_forgery.01",
        "shallow_cipher.01",
    ):
        assert kept in small_ids
    assert small_ids < frontier_ids  # a strict subset: gating only ever removes


def test_profile_gating_is_a_predicate_not_a_model_table() -> None:
    """An unknown model id must still produce a usable technique set — the
    tier is inferred from the id, so a model OpenRouter adds tomorrow needs no
    code change. Unknown falls back to `frontier` (run everything), because
    running an inapplicable technique costs one call while skipping an
    applicable one costs a missed break."""
    assert profile_for_model("") == "frontier"
    assert profile_for_model("some-vendor/brand-new-model") == "frontier"
    assert len(techniques_for_model("some-vendor/brand-new-model")) == len(TECHNIQUES)
    # A cheap tier is recognised by the id alone, vendor prefix irrelevant.
    assert profile_for_model("meta-llama/llama-3.1-8b-instruct") == "small"


def test_gating_preserves_library_order_so_instantiation_stays_byte_identical() -> None:
    gated = techniques_for_model("qwen/qwen3.8-flash")
    assert list(gated) == [t for t in TECHNIQUES if t in gated]


def test_format_is_the_exactly_one_technique_fixture_category() -> None:
    """Several runner tests (`test_budget_caps`, `test_sse`, `test_gaps`,
    `test_surfaces_runner`, `test_report`, `test_tracer`) need a rule
    category that matches EXACTLY ONE single-turn technique on the chat
    surface, so their scripted response counts are exact and
    hand-verifiable. `format` is that category — and it has to hold for the
    profile-GATED set too, since that is what the runner instantiates. This
    test is what stops a new technique from silently breaking six files at
    once by picking up `format` as a target."""
    for techniques in (TECHNIQUES, techniques_for_model("qwen/qwen3.8-flash")):
        matches = [t for t in techniques if "format" in t.targets and "chat" in t.surfaces]
        assert [t.id for t in matches] == ["debug_pretext.01"]
        assert not matches[0].turns  # single-turn
        assert matches[0].prefill is None  # one dispatch, no forged turn


def test_verbatim_extraction_has_all_six_shapes() -> None:
    shapes = {t.id for t in TECHNIQUES if t.family == "verbatim_extraction"}
    assert len(shapes) == 6


async def test_seed_techniques_is_idempotent(clean_db: Database) -> None:
    first = await seed_techniques(clean_db)
    second = await seed_techniques(clean_db)
    assert first == len(TECHNIQUES)
    assert second == len(TECHNIQUES)

    async with clean_db.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM techniques")
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM techniques")}

    assert count == len(TECHNIQUES)
    assert ids == {t.id for t in TECHNIQUES}


async def test_seed_techniques_round_trips_targets_and_surfaces(clean_db: Database) -> None:
    await seed_techniques(clean_db)
    sample = TECHNIQUES[0]
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM techniques WHERE id = $1", sample.id)
    assert row is not None
    assert row["family"] == sample.family
    assert list(row["targets"]) == list(sample.targets)
    assert list(row["surfaces"]) == list(sample.surfaces)
    assert row["template"] == sample.template
    assert row["canary"] == sample.canary
    assert row["licence"] == sample.licence
    assert row["source"] == sample.source
