"""substrate: durable job queue

Revision ID: 17111b16b3be
Revises:
Create Date: 2026-08-28 17:58:56.671736

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "17111b16b3be"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE job_state AS ENUM
            ('pending', 'running', 'succeeded', 'failed', 'dead')
    """)

    op.execute("""
        CREATE TABLE jobs (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            queue           TEXT      NOT NULL,
            kind            TEXT      NOT NULL,
            payload         JSONB     NOT NULL DEFAULT '{}'::jsonb,

            -- Dedupe at enqueue time. NULL means 'no dedupe for this job'.
            idempotency_key TEXT,

            state           job_state NOT NULL DEFAULT 'pending',
            priority        SMALLINT  NOT NULL DEFAULT 0,

            attempts        INTEGER   NOT NULL DEFAULT 0,
            max_attempts    INTEGER   NOT NULL DEFAULT 5,

            -- The single scheduling column. Before claim: when the job may
            -- run. After claim: when its lease expires. One column does
            -- delayed execution, retry backoff AND visibility timeout.
            available_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- Fencing token. Incremented on every claim, so a worker whose
            -- lease was stolen can be told 'you no longer own this'.
            lease_epoch     INTEGER   NOT NULL DEFAULT 0,
            claimed_by      TEXT,

            last_error      TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at     TIMESTAMPTZ,

            CONSTRAINT jobs_max_attempts_positive CHECK (max_attempts >= 1)
        )
    """)

    # Enqueueing the same logical work twice is a no-op. Partial, so the
    # common case (key IS NULL) costs nothing and never collides.
    op.execute("""
        CREATE UNIQUE INDEX jobs_idempotency_uniq
            ON jobs (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL
    """)

    # The claim path, and the only index that matters for throughput.
    # Partial on non-terminal states so finished jobs — eventually the vast
    # majority of the table — are not in the index at all.
    op.execute("""
        CREATE INDEX jobs_claim_idx
            ON jobs (queue, priority DESC, available_at, id)
            WHERE state IN ('pending', 'running')
    """)

    op.execute("""
        CREATE INDEX jobs_dead_idx
            ON jobs (queue, finished_at DESC)
            WHERE state = 'dead'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TYPE IF EXISTS job_state")
