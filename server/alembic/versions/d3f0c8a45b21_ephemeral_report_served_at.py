"""snag: projects.report_served_at (PRIV-02 gap closure)

Revision ID: d3f0c8a45b21
Revises: a1c4f0e9b217
Create Date: 2026-08-29 00:00:00.000000

01-VERIFICATION.md found PRIV-02 ("ephemeral mode persists nothing
server-side") only partially closed: 01-06 withholds `prompt_versions` and
`projects.tools_json` for an ephemeral project, but `rules`, `surfaces`,
`scans`, and `attack_runs` (full real conversation transcripts) were written
durably with no automatic cleanup, and the frontend never calls
`DELETE /api/projects/{slug}` — 01-06-SUMMARY.md's own "Next Phase Readiness"
note flagged this exact follow-up. This column is the purge clock: it is
stamped once (idempotently) by `snag.report.mark_report_served` the first
time an ephemeral, non-seeded project's report is read AFTER a scan has
actually finished, and read by `snag.report.purge_expired_ephemeral` to
hard-delete the project (cascading to every child table) once
`ephemeral_grace_seconds` has elapsed since that stamp.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f0c8a45b21"
down_revision: str | Sequence[str] | None = "a1c4f0e9b217"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN report_served_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS report_served_at")
