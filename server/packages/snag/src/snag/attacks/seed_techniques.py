"""Seeds `TECHNIQUES` into the global `techniques` table (§12 schema,
`b49dfb973917_snag_full_schema`). Global, not per-project — one library
shared by every scan. Idempotent by design: an upsert on the primary key,
so re-running the seeder (a redeploy, a second `snag` CLI invocation) never
duplicates a row or errors on a rerun.
"""

from __future__ import annotations

from snag.attacks.library import TECHNIQUES
from substrate.db import Database

_UPSERT = """
    INSERT INTO techniques (id, family, targets, surfaces, template, turns, canary, licence, source)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    ON CONFLICT (id) DO UPDATE SET
        family   = EXCLUDED.family,
        targets  = EXCLUDED.targets,
        surfaces = EXCLUDED.surfaces,
        template = EXCLUDED.template,
        turns    = EXCLUDED.turns,
        canary   = EXCLUDED.canary,
        licence  = EXCLUDED.licence,
        source   = EXCLUDED.source
"""


async def seed_techniques(db: Database) -> int:
    """Upsert every `TECHNIQUES` record. Returns the number of records
    seeded (not necessarily the number of rows changed — an unchanged
    rerun still counts as seeded)."""
    async with db.acquire() as conn, conn.transaction():
        for technique in TECHNIQUES:
            await conn.execute(
                _UPSERT,
                technique.id,
                technique.family,
                list(technique.targets),
                list(technique.surfaces),
                technique.template,
                list(technique.turns),
                technique.canary,
                technique.licence,
                technique.source,
            )
    return len(TECHNIQUES)
