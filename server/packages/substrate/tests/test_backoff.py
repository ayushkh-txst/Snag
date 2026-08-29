"""Backoff timing, including the property that motivates jitter."""

from __future__ import annotations

import random
import statistics

from hypothesis import given
from hypothesis import strategies as st

from substrate.resilience import full_jitter_delay


@given(
    attempt=st.integers(min_value=1, max_value=64),
    base=st.floats(min_value=0.01, max_value=5.0),
    cap=st.floats(min_value=0.1, max_value=300.0),
)
def test_delay_is_always_within_its_window(attempt: int, base: float, cap: float) -> None:
    """Never negative, never above the cap, never above the exponential."""
    delay = full_jitter_delay(attempt, base=base, cap=cap)
    ceiling = min(cap, base * 2.0 ** min(attempt - 1, 32))
    assert 0.0 <= delay <= ceiling + 1e-9


@given(attempt=st.integers(min_value=1, max_value=10))
def test_delay_never_exceeds_the_cap(attempt: int) -> None:
    assert full_jitter_delay(attempt, base=1.0, cap=5.0) <= 5.0 + 1e-9


def test_jitter_actually_spreads_the_herd() -> None:
    """The whole reason jitter exists, as an assertion."""
    rng = random.Random(1234)
    jittered = [full_jitter_delay(4, base=1.0, cap=60.0, rng=rng) for _ in range(1000)]
    deterministic = [1.0 * 2**3] * 1000

    assert statistics.pstdev(deterministic) == 0.0
    assert statistics.pstdev(jittered) > 1.5
    assert max(jittered) <= 8.0

    busiest = max(sum(1 for d in jittered if int(d * 10) == bucket) for bucket in range(80))
    assert busiest < 50, f"{busiest}/1000 clients retried in the same 100ms"
