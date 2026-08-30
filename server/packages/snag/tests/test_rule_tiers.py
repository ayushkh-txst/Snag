"""Which tier a rule is attacked in must follow from its CHECKER.

The two queries used to split on `testable`, on the stated assumption that
the tiers were "disjoint by construction — a rule is inserted with
`testable = (checker_type != 'none')`". The rules screen lets a person
toggle `testable`, so that construction does not hold: ticking a rule that
has no mechanical checker moved it into the mechanical tier, where the only
checker available is the `none` no-op.

Live, scan 10: two ticked rules with `checker_type = 'none'` took 50 attacks
between them and every single one came back "not testable by code — needs
human review". Fifty paid model calls that scored nothing, on the two rules
the judge exists to cover.
"""

from __future__ import annotations

import pytest

from snag.runner import select_rule_tiers
from substrate.db import Database


async def _rule(db: Database, slug: str, checker_type: str, testable: bool) -> int:
    async with db.acquire() as conn:
        return int(
            await conn.fetchval(
                """INSERT INTO rules (project_id, text, category, direction, source_line,
                                      checker_type, checker_config, testable, confidence)
                   VALUES ($1,'r','other','negative','r',$2,'{}'::jsonb,$3,0.9)
                   RETURNING id""",
                slug,
                checker_type,
                testable,
            )
        )


@pytest.fixture
async def project(clean_db: Database) -> str:
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model, seeded) VALUES ('p','m',false)")
    return "p"


async def test_a_ticked_rule_with_a_real_checker_is_mechanical(
    clean_db: Database, project: str
) -> None:
    rid = await _rule(clean_db, project, "forbidden_text", True)
    mech, judged = await select_rule_tiers(clean_db, project)
    assert [r["id"] for r in mech] == [rid]
    assert judged == []


async def test_a_ticked_rule_with_no_checker_goes_to_the_judge(
    clean_db: Database, project: str
) -> None:
    """The bug. Ticking it means "test this"; having no checker means code
    cannot, so the judge is the only tier that can say anything."""
    rid = await _rule(clean_db, project, "none", True)
    mech, judged = await select_rule_tiers(clean_db, project)
    assert [r["id"] for r in mech] == [], "nothing mechanical can score it"
    assert [r["id"] for r in judged] == [rid]


async def test_an_unticked_rule_with_no_checker_is_still_judged(
    clean_db: Database, project: str
) -> None:
    """The default state for a rule extraction could not express — unchanged."""
    rid = await _rule(clean_db, project, "none", False)
    _mech, judged = await select_rule_tiers(clean_db, project)
    assert [r["id"] for r in judged] == [rid]


async def test_unticking_a_rule_that_has_a_checker_drops_it_from_both(
    clean_db: Database, project: str
) -> None:
    """What unticking is for."""
    await _rule(clean_db, project, "forbidden_text", False)
    mech, judged = await select_rule_tiers(clean_db, project)
    assert mech == [] and judged == []


async def test_the_tiers_never_overlap(clean_db: Database, project: str) -> None:
    """Attacking a rule in both tiers would double every count it produces."""
    for checker in ("forbidden_text", "none", "no_prompt_leak"):
        for testable in (True, False):
            await _rule(clean_db, project, checker, testable)
    mech, judged = await select_rule_tiers(clean_db, project)
    assert not ({r["id"] for r in mech} & {r["id"] for r in judged})
