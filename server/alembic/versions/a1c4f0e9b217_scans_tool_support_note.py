"""snag: scans.tool_support_note (SIM-02)

Revision ID: a1c4f0e9b217
Revises: 5ef62807d01d
Create Date: 2026-08-28 20:00:00.000000

01-05 gave the runner a capability signal (`ToolsNotSupportedError`) for
when the chosen model rejects `tools` outright. 01-10 is what actually acts
on it: skip tool-surface attacks for that model rather than aborting the
scan. That skip has to be visible somewhere durable so the report (01-12)
can say honestly that tool-surface tests were not run for this model — a
log line alone (already emitted) does not reach the report payload.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4f0e9b217"
down_revision: str | Sequence[str] | None = "5ef62807d01d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE scans ADD COLUMN tool_support_note TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE scans DROP COLUMN IF EXISTS tool_support_note")
