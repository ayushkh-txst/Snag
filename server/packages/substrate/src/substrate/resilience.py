"""Retry timing. More primitives (token bucket, circuit breaker) land later."""

from __future__ import annotations

import random

_DEFAULT_RNG = random.SystemRandom()

# 2**33 seconds is already ~270 years; clamping the exponent keeps the
# intermediate float finite no matter how absurd `attempt` gets.
_MAX_EXPONENT = 32


def full_jitter_delay(
    attempt: int,
    *,
    base: float = 0.5,
    cap: float = 60.0,
    rng: random.Random | None = None,
) -> float:
    """Uniform random in [0, min(cap, base * 2**(attempt-1))].

    Why not plain exponential backoff:

    A provider returns 503 and a thousand clients all back off 1s, then 2s,
    then 4s. They were synchronized by the outage, so they stay synchronized —
    and they retry in a thundering herd at exactly the moments the service is
    trying to recover. Deterministic backoff reduces average load while
    keeping the PEAK load that caused the outage.

    Full jitter spreads each client uniformly across its whole window, so
    retries arrive smoothly. Expected delay is halved (E[U(0,x)] = x/2), which
    is a real cost — you trade latency for the absence of a coordinated spike.
    That trade is almost always correct, because the spike is what takes the
    service down.
    """
    exponent = min(max(attempt, 1) - 1, _MAX_EXPONENT)
    ceiling = min(cap, base * (2.0**exponent))
    return (rng or _DEFAULT_RNG).uniform(0.0, ceiling)
