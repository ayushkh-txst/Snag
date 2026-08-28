"""snag: scans.skipped_count (SCAN-03)

Revision ID: 5ef62807d01d
Revises: b49dfb973917
Create Date: 2026-08-28 19:00:00.000000

01-09's hard budget caps stop a scan before either cap is exceeded and must
record how many planned (rule x surface x technique x repeat) attempts never
ran, so the report can say what it didn't get to (§9.3, SCAN-03). There is
no existing column for this — add one rather than overloading an unrelated
counter.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5ef62807d01d"
down_revision: str | Sequence[str] | None = "b49dfb973917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE scans ADD COLUMN skipped_count INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE scans DROP COLUMN IF EXISTS skipped_count")
