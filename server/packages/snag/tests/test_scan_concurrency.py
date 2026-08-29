"""Bounded, adaptive concurrency in the scan runner. A scan is hundreds of
model calls at 7-13s each; run one at a time that is ~90 minutes, so the runner
dispatches attack-repeat units concurrently instead. The budget cap must still
be enforced BEFORE every dispatch even with many units racing, the ATTACK SET
must be unchanged (only the order replies arrive in differs), and a multi-turn
attack's own turns must still go out in order.

Every cap assertion is against dispatches actually made (`fake.calls` /
`fake.max_in_flight`), not just the final DB counters — a bug that lets one
extra call through under overlap is caught even if the counters end up looking
right.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest

from snag import cost as cost_module
from snag import runner
from snag.attacks.instantiate import Rule as AttackRule
from snag.attacks.instantiate import Surface as AttackSurface
from snag.attacks.instantiate import instantiate
from snag.attacks.library import techniques_for_model
from snag.config import get_settings
from snag.cost import ModelPricing
from substrate.db import Database
from substrate.llm import CompletionResponse, RetryListening, StopReason, TokenUsage

MODEL = "qwen/qwen3.8-flash"
PER_CALL_COST = Decimal("0.002")  # 800*0.000001 + 400*0.000003, matching _prime_pricing_cache


@pytest.fixture(autouse=True)
def _prime_pricing_cache() -> Iterator[None]:
    cost_module._PRICING_CACHE[MODEL] = ModelPricing(
        model=MODEL,
        prompt_per_token=Decimal("0.000001"),
        completion_per_token=Decimal("0.000003"),
    )
    yield
    cost_module._PRICING_CACHE.pop(MODEL, None)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    # Each test that pins SCAN_CONCURRENCY leaves the process-wide settings
    # cache holding its value; clear it after so the next test/module starts
    # from the real environment again.
    yield
    get_settings.cache_clear()


def _set_concurrency(monkeypatch: pytest.MonkeyPatch, ceiling: int) -> None:
    monkeypatch.setenv("SCAN_CONCURRENCY", str(ceiling))
    get_settings.cache_clear()


def _safe_response() -> CompletionResponse:
    return CompletionResponse(
        text="Sure, happy to help.",
        usage=TokenUsage(20, 10),
        stop_reason=StopReason.END_TURN,
        model=MODEL,
        cost_usd=PER_CALL_COST,
    )


class _ConcurrentFake:
    """A completions double that actually yields control (an `asyncio.sleep`)
    so units genuinely overlap, and records the peak number in flight at once —
    the only way to prove the cap holds under real concurrency rather than under
    the accidental serialisation an instant fake produces. Never runs out of
    responses, so tests assert on `calls`/`max_in_flight`, not a script."""

    def __init__(self, *, delay: float = 0.01) -> None:
        self.calls: list[object] = []
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete(self, request: object) -> CompletionResponse:
        self.calls.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return _safe_response()
        finally:
            self.in_flight -= 1


class _RateLimitingFake(_ConcurrentFake):
    """A double that also speaks `RetryListening` and fires the rate-limit
    signal on a chosen call, standing in for a real adapter seeing a 429."""

    def __init__(self, *, trip_on_call: int, delay: float = 0.01) -> None:
        super().__init__(delay=delay)
        self._listeners: list[Callable[[], None]] = []
        self._trip_on_call = trip_on_call
        self.subscribe_count = 0

    def add_retry_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        self.subscribe_count += 1

        def _cancel() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _cancel

    @property
    def active_listeners(self) -> int:
        return len(self._listeners)

    async def complete(self, request: object) -> CompletionResponse:
        if len(self.calls) + 1 == self._trip_on_call:
            for listener in list(self._listeners):
                listener()
        return await super().complete(request)


async def _make_single_turn_project(db: Database, *, slug: str) -> None:
    """One rule/one surface matching EXACTLY ONE single-turn technique
    (`format` category on `chat`), so `len(attacks) == 1` and every repeat is
    exactly one dispatch — the same fixture convention as test_budget_caps."""
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, MODEL)
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, checker_type,
                                   checker_config, testable)
               VALUES ($1, 'always reply in the house format', 'format', 'negative',
                       'forbidden_text', $2, true) RETURNING id""",
            slug,
            {"strings": ["this-never-matches-anything"]},
        )
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
               VALUES ($1, 'chat', 'user message', true, true) RETURNING id""",
            slug,
        )
    attacks = instantiate(
        [AttackRule(id=str(rule_id), text="x", category="format", direction="negative")],
        [AttackSurface(id=str(surface_id), kind="chat", path="user message")],
        techniques_for_model(MODEL),
    )
    assert len(attacks) == 1
    assert not isinstance(attacks[0].prompt_or_turns, tuple)


async def _make_matrix_project(db: Database, *, slug: str) -> None:
    """A richer project — two rules across categories, two surfaces — so the
    attack matrix has many distinct (rule, surface, technique) attacks, single-
    and multi-turn both. Every rule uses `forbidden_text` with a string no reply
    contains, so every verdict is deterministically 'held' regardless of reply
    text or the order units complete in."""
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, MODEL)
        for text, category in (
            ("always reply in the house format", "format"),
            ("never step outside your scope", "scope_boundary"),
        ):
            await conn.execute(
                """INSERT INTO rules (project_id, text, category, direction, checker_type,
                                       checker_config, testable)
                   VALUES ($1, $2, $3, 'negative', 'forbidden_text', $4, true)""",
                slug,
                text,
                category,
                {"strings": ["this-never-matches-anything"]},
            )
        for path in ("user message", "support chat widget"):
            await conn.execute(
                """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
                   VALUES ($1, 'chat', $2, true, true)""",
                slug,
                path,
            )


async def _insert_pending_scan(
    db: Database,
    *,
    slug: str,
    repeats: int,
    call_cap: int | None,
    spend_cap: Decimal | None,
    surfaces: list[str] | None = None,
) -> int:
    async with db.acquire() as conn:
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, surfaces, models, status,
                                   call_cap, spend_cap)
               VALUES ($1, 'custom', $2, $3, $4, 'pending', $5, $6) RETURNING id""",
            slug,
            repeats,
            surfaces or ["direct"],
            [MODEL],
            call_cap,
            spend_cap,
        )
    return int(scan_id)


async def _attack_run_identity(db: Database, scan_id: int) -> list[tuple[object, ...]]:
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT rule_id, surface_id, technique_id, family, repeat_index,
                      verdict_tier, passed, applicable
               FROM attack_runs WHERE scan_id = $1""",
            scan_id,
        )
    return sorted(
        (
            r["rule_id"], r["surface_id"], r["technique_id"], r["family"],
            r["repeat_index"], r["verdict_tier"], r["passed"], r["applicable"],
        )
        for r in rows
    )


# ----------------------------------------------------------- adaptive limiter


async def test_limiter_starts_below_ceiling_and_climbs_to_it() -> None:
    limiter = runner._AdaptiveLimiter(ceiling=5, start=2)
    assert limiter.allowed == 2
    for _ in range(20):
        limiter.record_success()
    assert limiter.allowed == 5  # additive increase, never past the ceiling


async def test_limiter_start_is_clamped_to_ceiling() -> None:
    assert runner._AdaptiveLimiter(ceiling=1, start=3).allowed == 1


async def test_limiter_halves_on_rate_limit_with_floor_of_one() -> None:
    limiter = runner._AdaptiveLimiter(ceiling=16, start=16)
    limiter.record_rate_limited()
    assert limiter.allowed == 8
    limiter.record_rate_limited()
    assert limiter.allowed == 4
    for _ in range(5):
        limiter.record_rate_limited()
    assert limiter.allowed == 1  # never zero — that would deadlock the gate


async def test_limiter_never_admits_more_than_allowed_at_once() -> None:
    limiter = runner._AdaptiveLimiter(ceiling=10, start=2)
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        await limiter.acquire()
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        limiter.release()

    # No success signal is fed, so `allowed` stays at the start value and the
    # six workers can never be more than two in flight together.
    await asyncio.gather(*[worker() for _ in range(6)])
    assert peak == 2


# ------------------------------------------------------ caps under concurrency


async def test_call_cap_holds_under_true_concurrency(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_concurrency(monkeypatch, 6)
    slug = "proj-cc-call-cap"
    await _make_single_turn_project(clean_db, slug=slug)
    repeats = 8
    call_cap = 3
    scan_id = await _insert_pending_scan(
        clean_db, slug=slug, repeats=repeats, call_cap=call_cap, spend_cap=None
    )

    fake = _ConcurrentFake(delay=0.02)
    await runner.run_scan(clean_db, scan_id, completions=fake)

    # Never past the cap, even though far more units than the cap were launched
    # and genuinely overlapped (proved by max_in_flight > 1).
    assert len(fake.calls) == call_cap
    assert 1 < fake.max_in_flight <= call_cap

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
        run_count = await conn.fetchval(
            "SELECT count(*) FROM attack_runs WHERE scan_id = $1", scan_id
        )
    assert row["status"] == "stopped_at_cap"
    assert row["call_count"] == call_cap
    assert run_count == call_cap
    assert row["attacks_done"] == call_cap
    assert row["skipped_count"] == repeats - call_cap


async def test_spend_cap_holds_under_true_concurrency(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_concurrency(monkeypatch, 6)
    slug = "proj-cc-spend-cap"
    await _make_single_turn_project(clean_db, slug=slug)
    repeats = 8
    # Three calls fit ($0.006); a fourth would not ($0.008 > $0.007).
    spend_cap = Decimal("0.007")
    scan_id = await _insert_pending_scan(
        clean_db, slug=slug, repeats=repeats, call_cap=None, spend_cap=spend_cap
    )

    fake = _ConcurrentFake(delay=0.02)
    await runner.run_scan(clean_db, scan_id, completions=fake)

    assert len(fake.calls) == 3
    assert 1 < fake.max_in_flight <= 3
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
    assert row["status"] == "stopped_at_cap"
    assert Decimal(row["cost"]) == PER_CALL_COST * 3
    assert row["skipped_count"] == repeats - 3


# ------------------------------------------------- the attack set is unchanged


async def test_concurrency_does_not_change_the_attack_run_set(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same project, same config, run once effectively sequentially (ceiling 1)
    and once with a wide ceiling: the SET of attack_runs must be identical —
    same rules x surfaces x techniques x repeats, same verdicts — differing at
    most in the order rows were written."""
    slug = "proj-cc-same-set"
    await _make_matrix_project(clean_db, slug=slug)

    _set_concurrency(monkeypatch, 1)
    seq_scan = await _insert_pending_scan(
        clean_db, slug=slug, repeats=2, call_cap=1000, spend_cap=Decimal("100"),
        surfaces=["direct", "multiturn"],
    )
    await runner.run_scan(clean_db, seq_scan, completions=_ConcurrentFake(delay=0.0))
    sequential = await _attack_run_identity(clean_db, seq_scan)

    _set_concurrency(monkeypatch, 8)
    conc_scan = await _insert_pending_scan(
        clean_db, slug=slug, repeats=2, call_cap=1000, spend_cap=Decimal("100"),
        surfaces=["direct", "multiturn"],
    )
    await runner.run_scan(clean_db, conc_scan, completions=_ConcurrentFake(delay=0.005))
    concurrent = await _attack_run_identity(clean_db, conc_scan)

    assert sequential  # the matrix actually produced runs
    # Identity is compared on (rule, surface, technique, family, repeat, tier,
    # verdict, applicable) — scan_id aside, the two runs are the same set.
    assert concurrent == sequential


# ---------------------------------------------------- within-unit call order


async def test_multi_turn_attack_dispatches_its_turns_in_order(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrency is ACROSS attacks, never within one: a multi-turn ladder's
    rungs still go out strictly in order, each carrying the full history grown
    by the previous reply. Run one multi-turn attack and check the requests it
    made have a strictly increasing message count."""
    _set_concurrency(monkeypatch, 6)
    slug = "proj-cc-multiturn-order"
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, MODEL)
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, checker_type,
                                   checker_config, testable)
               VALUES ($1, 'never break scope', 'scope_boundary', 'negative',
                       'forbidden_text', $2, true) RETURNING id""",
            slug,
            {"strings": ["this-never-matches-anything"]},
        )
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
               VALUES ($1, 'chat', 'user message', true, true) RETURNING id""",
            slug,
        )

    attacks = instantiate(
        [AttackRule(id=str(rule_id), text="x", category="scope_boundary", direction="negative")],
        [AttackSurface(id=str(surface_id), kind="chat", path="user message")],
        techniques_for_model(MODEL),
    )
    multi_turn = [a for a in attacks if isinstance(a.prompt_or_turns, tuple)]
    assert multi_turn, "fixture assumption: scope_boundary/chat matches a multi-turn technique"
    turns = multi_turn[0].prompt_or_turns
    assert isinstance(turns, tuple) and len(turns) >= 2

    scan_id = await _insert_pending_scan(
        clean_db, slug=slug, repeats=1, call_cap=1000, spend_cap=Decimal("100")
    )
    fake = _ConcurrentFake(delay=0.0)
    await runner.run_scan(
        clean_db, scan_id, completions=fake, only_attacks=[multi_turn[0].key()]
    )

    # One call per scripted turn (the gap-probe pass uses its own `gap:` run_id
    # and is filtered out), each with a strictly larger message history than the
    # last — the rungs went out in order, not interleaved.
    turn_calls = [c for c in fake.calls if c.run_id == f"scan:{scan_id}"]  # type: ignore[attr-defined]
    assert len(turn_calls) == len(turns)
    lengths = [len(c.messages) for c in turn_calls]  # type: ignore[attr-defined]
    assert lengths == sorted(lengths)
    assert len(set(lengths)) == len(lengths)


# ----------------------------------------------- adaptive signal is wired in


async def test_run_scan_subscribes_and_unsubscribes_rate_limit_signal(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completions adapter that can report 429s (`RetryListening`) has the
    limiter's decrease hook wired to it for the scan and torn down after —
    a `FakeCompletions`, which cannot, is simply left as a fixed-ceiling bound."""
    _set_concurrency(monkeypatch, 6)
    slug = "proj-cc-adaptive"
    await _make_single_turn_project(clean_db, slug=slug)
    scan_id = await _insert_pending_scan(
        clean_db, slug=slug, repeats=5, call_cap=1000, spend_cap=Decimal("100")
    )

    fake = _RateLimitingFake(trip_on_call=2, delay=0.005)
    assert isinstance(fake, RetryListening)
    await runner.run_scan(clean_db, scan_id, completions=fake)

    assert fake.subscribe_count == 1  # subscribed exactly once for the scan
    assert fake.active_listeners == 0  # and cancelled when the scan finished
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM scans WHERE id = $1", scan_id)
    assert row["status"] == "completed"  # the 429 signal slowed, never failed, the scan
