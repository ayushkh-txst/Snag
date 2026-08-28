"""Everything expensive, built once at startup and shared read-only.

Mirrors citedelta.api.state (CiteDelta-RAG/packages/citedelta/src/citedelta/api/state.py):
one `AppState` built in `lifespan`, read via `request.app.state.ctx` — never
rebuilt per request.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from snag.config import Settings
from substrate.db import Database
from substrate.llm.pricing import CostLedger

log = structlog.get_logger(__name__)


@dataclass
class AppState:
    db: Database
    settings: Settings
    ledger: CostLedger
    """Shared across every completion this process makes, so a scan's
    reported cost is real spend, not a per-call guess."""


async def build_state(settings: Settings) -> AppState:
    """Load once. Everything here is read-only afterwards, which is what
    makes it safe to share across concurrent requests without a lock."""
    # Construct and connect explicitly — see substrate.db.Database.open's own
    # docstring reasoning: going through the async context manager here would
    # tear the pool back down the moment this function returns.
    db = Database(settings.database_url)
    await db.connect()
    log.info("api.state_built")
    return AppState(db=db, settings=settings, ledger=CostLedger())


async def close_state(state: AppState) -> None:
    await state.db.close()
