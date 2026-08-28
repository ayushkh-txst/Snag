"""snag.gaps (GAP-01, GAP-02, T-13-01, T-13-02): the eight-item checklist,
behavioural probing with a mechanically-templated observation, the gap-
probe pass wired into the scan (`runner.py`), and its read endpoint.
`FakeCompletions` throughout; no live network.

Task 1 (checklist / probe / templated observation) and Task 2 (scan pass /
endpoint) coverage lives in this one file per the plan's own `<verify>`
`-k` filters.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager
from decimal import Decimal

import httpx
import pytest

from snag import cost as cost_module
from snag import runner
from snag.attacks.instantiate import Rule as AttackRule
from snag.attacks.instantiate import Surface as AttackSurface
from snag.attacks.instantiate import instantiate
from snag.cost import ModelPricing
from snag.gaps import (
    GAP_CHECKLIST,
    GapObservationFacts,
    probe_gap,
    template_observation,
)
from substrate.db import Database
from substrate.llm import (
    CompletionRequest,
    CompletionResponse,
    Completions,
    FakeCompletions,
    StopReason,
    TokenUsage,
    ToolCall,
)
from substrate.queue import Worker

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]
DrainScanQueue = Callable[[Database, Completions], Awaitable[Worker]]

MODEL = "qwen/qwen3.8-flash"

_EXPECTED_KEYS = {
    "tool_failure",
    "empty_result",
    "out_of_scope",
    "guessing_when_unsure",
    "personal_data",
    "hostile_users",
    "conflicting_instructions",
    "uncovered_situations",
}

_TOOL_FAILURE_ITEM = next(i for i in GAP_CHECKLIST if i.key == "tool_failure")
_HOSTILE_ITEM = next(i for i in GAP_CHECKLIST if i.key == "hostile_users")


@pytest.fixture(autouse=True)
def _prime_pricing_cache() -> Iterator[None]:
    """`run_scan` makes exactly ONE pre-dispatch cost estimate before its
    loop starts — priming the process-level cache here means that estimate
    never touches the real network (mirrors test_runner.py)."""
    cost_module._PRICING_CACHE[MODEL] = ModelPricing(
        model=MODEL,
        prompt_per_token=Decimal("0.000001"),
        completion_per_token=Decimal("0.000003"),
    )
    yield
    cost_module._PRICING_CACHE.pop(MODEL, None)


def _response(text: str, *, tool_calls: tuple[ToolCall, ...] = ()) -> CompletionResponse:
    stop = StopReason.TOOL_USE if tool_calls else StopReason.END_TURN
    return CompletionResponse(
        text=text, usage=TokenUsage(20, 10), stop_reason=stop, model=MODEL, tool_calls=tool_calls
    )


def _safe_response() -> CompletionResponse:
    """A reply that shouldn't trip ANY of the eight items' risk patterns —
    used to script a whole scan (attack matrix + gap pass) without caring
    about each gap's individual verdict."""
    return _response("Sure, happy to help with that.")


async def _make_project(
    db: Database, *, slug: str, system_prompt: str = "Be nice. Never do X."
) -> None:
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, MODEL)
        await conn.execute(
            "INSERT INTO prompt_versions (project_id, full_text) VALUES ($1, $2)",
            slug,
            system_prompt,
        )


async def _add_rule(db: Database, slug: str) -> int:
    async with db.acquire() as conn:
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, checker_type,
                                   checker_config, testable)
               VALUES ($1, 'never break character', 'tone_style', 'negative',
                       'forbidden_text', $2, true) RETURNING id""",
            slug,
            {"strings": ["this-never-matches-anything"]},
        )
    return int(rule_id)


async def _add_chat_surface(db: Database, slug: str) -> int:
    async with db.acquire() as conn:
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
               VALUES ($1, 'chat', 'user message', true, true) RETURNING id""",
            slug,
        )
    return int(surface_id)


def _single_chat_attack_count(db_rule_id: int, db_surface_id: int) -> int:
    attacks = instantiate(
        [AttackRule(id=str(db_rule_id), text="x", category="tone_style", direction="negative")],
        [AttackSurface(id=str(db_surface_id), kind="chat", path="user message")],
    )
    assert len(attacks) == 1  # fixture assumption, mirrors test_budget_caps.py
    return len(attacks)


# --------------------------------------------------------------------- Task 1
# (checklist / probe / templated observation)


def test_gap_checklist_contains_all_eight_checklist_items() -> None:
    assert len(GAP_CHECKLIST) == 8
    assert {item.key for item in GAP_CHECKLIST} == _EXPECTED_KEYS


def test_gap_checklist_items_each_carry_a_unique_non_empty_probe() -> None:
    probes = [item.probe for item in GAP_CHECKLIST]
    assert all(probe.strip() for probe in probes)
    assert len(set(probes)) == len(probes)


def test_template_observation_never_calls_the_model() -> None:
    """Structural guarantee for GAP-02: `template_observation`'s own source
    contains no `.complete(` call and no `await` — it cannot possibly make
    a model call, by construction."""
    source = inspect.getsource(template_observation)
    assert ".complete(" not in source
    assert "await" not in source


def test_template_observation_is_deterministic_given_the_same_facts() -> None:
    facts = GapObservationFacts(
        tool_called=False,
        tool_names=(),
        guarded=False,
        risky=True,
        risk_phrase="invented a delivery date",
        guard_phrase="told the user it couldn't confirm",
        matched_evidence="tomorrow",
    )
    first = template_observation(facts)
    second = template_observation(facts)
    assert first == second
    assert "invented a delivery date" in first
    assert "'tomorrow'" in first


def test_template_observation_reports_the_guard_phrase_when_covered() -> None:
    facts = GapObservationFacts(
        tool_called=False,
        tool_names=(),
        guarded=True,
        risky=False,
        risk_phrase="invented a delivery date",
        guard_phrase="told the user it couldn't confirm",
        matched_evidence=None,
    )
    assert template_observation(facts) == "It told the user it couldn't confirm."


def test_template_observation_names_the_tool_when_one_fired_and_it_was_guarded() -> None:
    facts = GapObservationFacts(
        tool_called=True,
        tool_names=("order_lookup",),
        guarded=True,
        risky=False,
        risk_phrase="x",
        guard_phrase="asked before doing anything else",
        matched_evidence=None,
    )
    observed = template_observation(facts)
    assert observed == "It called order_lookup and asked before doing anything else."


def test_template_observation_falls_back_when_neither_signal_fires() -> None:
    facts = GapObservationFacts(
        tool_called=False,
        tool_names=(),
        guarded=False,
        risky=False,
        risk_phrase="x",
        guard_phrase="y",
        matched_evidence=None,
    )
    assert "no mechanical signal" in template_observation(facts)


async def test_probe_gap_sets_covered_true_when_the_model_flags_the_gap_explicitly() -> None:
    fake = FakeCompletions(
        responses=[_response("I'm sorry, I can't confirm the delivery date right now.")]
    )
    result = await probe_gap(fake, project=None, item=_TOOL_FAILURE_ITEM, model=MODEL)
    assert result.covered is True
    assert result.verdict.startswith("Covered")
    assert len(fake.calls) == 1


async def test_probe_gap_sets_covered_false_when_the_model_fabricates_a_value() -> None:
    fake = FakeCompletions(responses=[_response("No worries, it should arrive tomorrow!")])
    result = await probe_gap(fake, project=None, item=_TOOL_FAILURE_ITEM, model=MODEL)
    assert result.covered is False
    assert "invented a delivery date" in result.observed
    assert result.verdict.startswith("Gap")


async def test_probe_gap_treats_a_fired_tool_call_as_the_risk_signal_when_configured() -> None:
    call = ToolCall(id="1", name="issue_refund", arguments={"order_id": "9999"})
    fake = FakeCompletions(responses=[_response("Done — processed.", tool_calls=(call,))])
    result = await probe_gap(
        fake,
        project=None,
        item=_HOSTILE_ITEM,
        model=MODEL,
        tools=({"type": "function", "function": {"name": "issue_refund", "parameters": {}}},),
    )
    assert result.covered is False
    assert "issue_refund" in result.observed


async def test_probe_gap_routes_through_a_caller_supplied_dispatch_function() -> None:
    fake = FakeCompletions(responses=[_response("Sure.")])
    seen: list[CompletionRequest] = []

    async def recording_dispatch(
        client: Completions, request: CompletionRequest
    ) -> CompletionResponse:
        seen.append(request)
        return await client.complete(request)

    result = await probe_gap(
        fake, project=None, item=_TOOL_FAILURE_ITEM, model=MODEL, dispatch=recording_dispatch
    )
    assert len(seen) == 1
    assert len(fake.calls) == 1  # the dispatch function is the ONLY path to `.complete`
    assert result.probe == _TOOL_FAILURE_ITEM.probe


async def test_probe_gap_uses_the_projects_id_in_the_run_id() -> None:
    fake = FakeCompletions(responses=[_response("Sure.")])
    await probe_gap(fake, project={"id": "proj-x"}, item=_TOOL_FAILURE_ITEM, model=MODEL)
    assert fake.last.run_id == f"gap:proj-x:{_TOOL_FAILURE_ITEM.key}"


# --------------------------------------------------------------------- Task 2
# (gap-probe pass in the scan / endpoint)


async def test_running_a_scan_produces_a_gap_row_for_every_checklist_item(
    clean_db: Database,
) -> None:
    slug = "proj-gaps-scan"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(clean_db, slug)
    surface_id = await _add_chat_surface(clean_db, slug)
    attack_count = _single_chat_attack_count(rule_id, surface_id)

    fake = FakeCompletions(
        responses=[_safe_response() for _ in range(attack_count + len(GAP_CHECKLIST))]
    )

    async with clean_db.acquire() as conn:
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, surfaces, models, status)
               VALUES ($1, 'quick', 1, $2, $3, 'pending') RETURNING id""",
            slug,
            ["direct"],
            [MODEL],
        )

    await runner.run_scan(clean_db, scan_id, completions=fake)

    async with clean_db.acquire() as conn:
        scan_row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
        gap_rows = await conn.fetch("SELECT * FROM gaps WHERE scan_id = $1 ORDER BY id", scan_id)

    assert scan_row["status"] == "completed"
    assert len(fake.calls) == attack_count + len(GAP_CHECKLIST)
    assert len(gap_rows) == len(GAP_CHECKLIST)
    assert {row["checklist_item"] for row in gap_rows} == {item.item for item in GAP_CHECKLIST}
    assert all(isinstance(row["covered"], bool) for row in gap_rows)
    assert all(row["project_id"] == slug for row in gap_rows)


async def test_gap_probe_pass_stops_at_the_same_hard_call_cap_as_the_attack_matrix(
    clean_db: Database,
) -> None:
    slug = "proj-gaps-cap"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(clean_db, slug)
    surface_id = await _add_chat_surface(clean_db, slug)
    attack_count = _single_chat_attack_count(rule_id, surface_id)

    call_cap = attack_count + 3  # stop partway through the 8-item gap pass
    fake = FakeCompletions(responses=[_safe_response() for _ in range(call_cap)])

    async with clean_db.acquire() as conn:
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, surfaces, models, status, call_cap)
               VALUES ($1, 'quick', 1, $2, $3, 'pending', $4) RETURNING id""",
            slug,
            ["direct"],
            [MODEL],
            call_cap,
        )

    await runner.run_scan(clean_db, scan_id, completions=fake)

    # Exactly the cap, not one more — the fake would have raised had a call
    # past it been attempted (SCAN-03, mirrors test_budget_caps.py).
    assert len(fake.calls) == call_cap

    async with clean_db.acquire() as conn:
        scan_row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
        gap_count = await conn.fetchval("SELECT count(*) FROM gaps WHERE scan_id = $1", scan_id)

    assert scan_row["status"] == "stopped_at_cap"
    assert gap_count == 3  # only the gap probes that fit under the cap were persisted


async def test_get_gaps_endpoint_returns_empty_list_when_the_project_has_no_scan_yet(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-gaps-empty"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/gaps")
    assert res.status_code == 200
    assert res.json() == []


async def test_get_gaps_endpoint_404s_for_an_unknown_project(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.get("/api/projects/does-not-exist/gaps")
    assert res.status_code == 404


async def test_get_gaps_endpoint_returns_the_ui_shape_with_a_real_covered_boolean(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    slug = "proj-gaps-endpoint"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(clean_db, slug)
    surface_id = await _add_chat_surface(clean_db, slug)
    attack_count = _single_chat_attack_count(rule_id, surface_id)

    fake = FakeCompletions(
        responses=[_safe_response() for _ in range(attack_count + len(GAP_CHECKLIST))]
    )

    async with client_factory(fake) as client:
        res = await client.post("/api/scans", json={"slug": slug, "mode": "quick"})
        assert res.status_code == 200, res.text

        worker = await drain_scan_queue(clean_db, fake)
        assert worker.processed == 1
        assert worker.failed == 0

        got = await client.get(f"/api/projects/{slug}/gaps")

    assert got.status_code == 200
    body = got.json()
    assert len(body) == len(GAP_CHECKLIST)
    for row in body:
        assert set(row) == {"id", "item", "probe", "observed", "verdict", "covered"}
        assert isinstance(row["covered"], bool)
