"""Per-IP guard on owner-funded scans, protecting the near-zero owner spend
requirement — a BYOK request funds itself and is never limited here.

Module-level state, single-process assumption documented and deliberate —
same pattern as CiteDelta's `_PENDING` (citedelta/api/app.py): this is a
demo-scale limiter appropriate to a single Render worker, not a distributed
one. A multi-instance deployment would need a shared store (Redis) instead
of this in-process dict; 01-09's hard budget caps are the defence-in-depth
layer that doesn't depend on this assumption (T-02-02).
"""

from __future__ import annotations

import time

from fastapi import HTTPException, Request

from snag.api.deps import resolve_key

# A demo-scale allowance: enough for someone trying the owner-funded path a
# few times in a sitting, nowhere near enough for a script to run up real
# spend from one IP.
DEFAULT_LIMIT = 5
DEFAULT_WINDOW_S = 3600.0

# ip -> ascending list of request timestamps (time.monotonic()) within the
# current sliding window. Single-process only — see module docstring.
_WINDOWS: dict[str, list[float]] = {}


def check_rate(ip: str, *, limit: int = DEFAULT_LIMIT, window_s: float = DEFAULT_WINDOW_S) -> None:
    """Sliding window keyed by `ip`. Drops timestamps older than `window_s`,
    then raises `HTTPException(429)` if `ip` is already at `limit` within
    the window; otherwise records this call and returns."""
    now = time.monotonic()
    window = _WINDOWS.setdefault(ip, [])
    cutoff = now - window_s
    while window and window[0] < cutoff:
        window.pop(0)
    if len(window) >= limit:
        raise HTTPException(
            status_code=429, detail="too many owner-funded scans from this IP — try again later"
        )
    window.append(now)


def guard_owner_scans(request: Request) -> None:
    """FastAPI dependency: engages `check_rate` only when this request's
    resolved key is the owner key (KEY-02). BYOK requests fund themselves
    and are never rate limited by this dependency."""
    resolution = resolve_key(request)
    if resolution.owner_funded:
        ip = request.client.host if request.client else "unknown"
        check_rate(ip)
