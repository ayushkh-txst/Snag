"""snag: attack_runs.applicable (01-18 — honest coverage)

Revision ID: c7a2e51d94f8
Revises: d3f0c8a45b21
Create Date: 2026-08-29 00:00:00.000000

An attack run could be recorded with `passed = true` while having tested
nothing at all: a canary checker (`instruction_isolation`,
`no_role_confusion`) paired with a technique that plants no canary returned
"no canary was planted by this attack — nothing to check", and that landed
in the report as "the rule HELD against this attack". Two of the
retail-support example's 25 runs were exactly this. It inflated the
denominator of every break rate (a rule reading 2/10 when only 8 attacks
actually tested anything) and the "attacks run" headline with it — the
opposite of what Snag claims to sell, honest coverage.

`applicable` is the third state: neither pass nor fail. The row is still
stored (the dispatch happened; its transcript is real and worth keeping)
but `snag.report` excludes it from BOTH numerator and denominator, as does
the per-rule attack tally in `/api/projects/{slug}/rules`.

The backfill re-classifies rows already written by the old code, matching
the exact sentinel `checker_output` those two checkers emit — so an
existing project's report tells the truth without needing a rescan.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a2e51d94f8"
down_revision: str | Sequence[str] | None = "d3f0c8a45b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE attack_runs ADD COLUMN applicable BOOLEAN NOT NULL DEFAULT true")
    op.execute(
        """UPDATE attack_runs SET applicable = false
           WHERE checker_output LIKE 'no canary was planted by this attack%'"""
    )


def downgrade() -> None:
    op.execute("ALTER TABLE attack_runs DROP COLUMN IF EXISTS applicable")
