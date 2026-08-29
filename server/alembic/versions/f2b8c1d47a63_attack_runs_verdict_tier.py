"""snag: attack_runs.verdict_tier and the disputed-break columns

Revision ID: f2b8c1d47a63
Revises: c7a2e51d94f8
Create Date: 2026-08-29 12:00:00.000000

Snag now decides a run one of two ways, and a report that cannot tell them
apart is worth less than one that shows only mechanical results.

`verdict_tier` says which:

* 'mechanical' — a checker from `snag.checkers` read the transcript. This is
  the trust anchor and stays primary; every existing row is one of these,
  which is exactly what the backfill asserts by defaulting the column.
* 'judged' — no checker in the registry could express the rule, so a
  stronger model scored it and had to quote the span it judged verbatim
  (`snag.judge`). Weaker evidence than a regex, honestly labelled as such.

`disputed`/`dispute_note`/`dispute_quote` are the other direction: a
MECHANICAL break over a descriptive phrase that the judge, on review,
believes the checker misread — a denial or a hypothetical the text search
counted as an assertion. The break is NOT deleted. The row keeps its
`passed = false` and its own evidence, and carries the disagreement
alongside so the report can group it separately and a person can settle it.
Dropping a real break to make a report look clean would be the same
dishonesty as reporting a fake one.

`dispute_quote` is a span copied verbatim out of the reply; a dispute
without one is never recorded (`snag.judge.review_batch`), so this column is
NULL exactly when `disputed` is false.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b8c1d47a63"
down_revision: str | Sequence[str] | None = "c7a2e51d94f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE attack_runs "
        "ADD COLUMN verdict_tier TEXT NOT NULL DEFAULT 'mechanical', "
        "ADD COLUMN disputed BOOLEAN NOT NULL DEFAULT false, "
        "ADD COLUMN dispute_note TEXT, "
        "ADD COLUMN dispute_quote TEXT"
    )
    # Every row written before this migration came from a checker, so the
    # column default IS the backfill — stated explicitly rather than left
    # implied, because "the default happened to be right" and "the existing
    # data was classified" are different claims.
    op.execute("UPDATE attack_runs SET verdict_tier = 'mechanical' WHERE verdict_tier IS NULL")
    op.execute(
        "ALTER TABLE attack_runs ADD CONSTRAINT attack_runs_verdict_tier_check "
        "CHECK (verdict_tier IN ('mechanical', 'judged'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE attack_runs DROP CONSTRAINT IF EXISTS attack_runs_verdict_tier_check")
    op.execute(
        "ALTER TABLE attack_runs "
        "DROP COLUMN IF EXISTS verdict_tier, "
        "DROP COLUMN IF EXISTS disputed, "
        "DROP COLUMN IF EXISTS dispute_note, "
        "DROP COLUMN IF EXISTS dispute_quote"
    )
