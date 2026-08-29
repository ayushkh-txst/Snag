"""§7.1/§7.2 acceptance: family coverage, canary discipline, licence
provenance, and idempotent seeding (ATTACK-01)."""

from __future__ import annotations

from pathlib import Path

from snag.attacks.library import FAMILIES, TECHNIQUES, needs_canary
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


def test_prefill_field_defaults_to_none_for_every_technique() -> None:
    """Phase-2 hook (report §S1): the field exists now, defaulting to None,
    so the runner change is a clean additive drop-in later."""
    for technique in TECHNIQUES:
        assert technique.prefill is None


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
