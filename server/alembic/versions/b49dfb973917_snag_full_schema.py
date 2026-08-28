"""snag: full §12 schema

Revision ID: b49dfb973917
Revises: 17111b16b3be
Create Date: 2026-08-28 17:58:56.890787

Every table from spec §12, raw SQL (no ORM models — mirrors CiteDelta-RAG's
alembic convention). `projects.id` IS the URL slug: unguessable
(secrets.token_urlsafe) for user projects, fixed for the six seeded examples
(01-15) — see T-01-05. UI reconciliation per architecture-plan.md: `surfaces.kind`
includes 'chat' (spec has 3 kinds) via an explicit CHECK; `gaps.covered` is a
real boolean, not the fragile `verdict.startsWith("Covered")` prefix contract
the UI mockup used.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b49dfb973917"
down_revision: str | Sequence[str] | None = "17111b16b3be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE projects (
            id         TEXT PRIMARY KEY,
            name       TEXT,
            model      TEXT NOT NULL,
            tools_json JSONB,
            ephemeral  BOOLEAN NOT NULL DEFAULT false,
            seeded     BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE prompt_versions (
            id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            full_text  TEXT NOT NULL,
            tools_json JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX prompt_versions_project_idx ON prompt_versions (project_id)")

    op.execute("""
        CREATE TABLE rules (
            id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            text               TEXT NOT NULL,
            category           TEXT NOT NULL,
            direction          TEXT NOT NULL,
            source_line        TEXT,
            checker_type       TEXT NOT NULL DEFAULT 'none',
            checker_config     JSONB,
            testable           BOOLEAN NOT NULL DEFAULT false,
            confidence         REAL,
            confirmed_by_user  BOOLEAN NOT NULL DEFAULT false,
            in_prompt          BOOLEAN NOT NULL DEFAULT true,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX rules_project_idx ON rules (project_id)")

    op.execute("""
        CREATE TABLE questions (
            id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            rule_id            BIGINT NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
            project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            round              INTEGER NOT NULL DEFAULT 1,
            text               TEXT NOT NULL,
            placeholder        TEXT,
            answer_raw         TEXT,
            answer_normalized  TEXT,
            status             TEXT NOT NULL DEFAULT 'open',
            conflict_note      TEXT
        )
    """)
    op.execute("CREATE INDEX questions_project_idx ON questions (project_id)")
    op.execute("CREATE INDEX questions_rule_idx ON questions (rule_id)")

    # 'chat' is Snag's addition to the spec's three surface kinds (template
    # slots, tool params, tool returns) — the chat box itself is always a
    # tested surface. See architecture-plan.md's "Data model" section.
    op.execute("""
        CREATE TABLE surfaces (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind            TEXT NOT NULL,
            path            TEXT NOT NULL,
            source          TEXT,
            risk            TEXT,
            user_controlled BOOLEAN NOT NULL DEFAULT true,
            note            TEXT,
            confirmed       BOOLEAN NOT NULL DEFAULT false,
            tests           INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT surfaces_kind_check
                CHECK (kind IN ('template_var', 'tool_param', 'tool_return', 'chat'))
        )
    """)
    op.execute("CREATE INDEX surfaces_project_idx ON surfaces (project_id)")

    op.execute("""
        CREATE TABLE scans (
            id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            prompt_version_id   BIGINT REFERENCES prompt_versions(id),
            mode                TEXT NOT NULL,
            repeats             INTEGER NOT NULL DEFAULT 1,
            surfaces            JSONB,
            models              JSONB,
            status              TEXT NOT NULL DEFAULT 'pending',
            call_count          INTEGER NOT NULL DEFAULT 0,
            cost                NUMERIC NOT NULL DEFAULT 0,
            call_cap            INTEGER,
            spend_cap           NUMERIC,
            label               TEXT,
            current_rule_id     BIGINT,
            current_surface_id  BIGINT,
            attacks_done        INTEGER NOT NULL DEFAULT 0,
            breaks_found        INTEGER NOT NULL DEFAULT 0,
            started_at          TIMESTAMPTZ,
            finished_at         TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX scans_project_idx ON scans (project_id)")

    # NOTE inline is the tracer seam (01-01 Task 3) — 01-09 replaces the
    # synchronous scan with a substrate.queue job, at which point this table
    # starts filling from a worker instead of a request handler.
    op.execute("""
        CREATE TABLE attack_runs (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            scan_id         BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            -- CASCADE here too (not just scan_id): a project delete cascades
            -- to `rules`/`surfaces` on its own project_id FK, and that path
            -- races the scan_id->attack_runs path within the same statement.
            -- Without this, Postgres can try to delete a `rules` row while
            -- an `attack_runs` row still points to it and raise a FK
            -- violation — caught by this migration's own cascade test.
            rule_id         BIGINT REFERENCES rules(id) ON DELETE CASCADE,
            surface_id      BIGINT REFERENCES surfaces(id) ON DELETE CASCADE,
            technique_id    TEXT NOT NULL,
            family          TEXT,
            model           TEXT NOT NULL,
            repeat_index    INTEGER NOT NULL DEFAULT 0,
            conversation    JSONB NOT NULL,
            passed          BOOLEAN NOT NULL,
            checker_output  TEXT,
            false_positive  BOOLEAN NOT NULL DEFAULT false,
            planted         TEXT,
            evidence        TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX attack_runs_scan_idx ON attack_runs (scan_id)")
    op.execute("CREATE INDEX attack_runs_rule_idx ON attack_runs (rule_id)")
    op.execute("CREATE INDEX attack_runs_surface_idx ON attack_runs (surface_id)")

    op.execute("""
        CREATE TABLE scan_events (
            id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            scan_id    BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            seq        BIGINT NOT NULL,
            kind       TEXT NOT NULL,
            data       JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX scan_events_scan_seq_idx ON scan_events (scan_id, seq)")

    op.execute("""
        CREATE TABLE gaps (
            id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            scan_id           BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            project_id        TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            checklist_item    TEXT NOT NULL,
            probe             TEXT,
            probe_transcript  JSONB,
            observed          TEXT,
            verdict           TEXT,
            covered           BOOLEAN NOT NULL DEFAULT false
        )
    """)
    op.execute("CREATE INDEX gaps_project_idx ON gaps (project_id)")
    op.execute("CREATE INDEX gaps_scan_idx ON gaps (scan_id)")

    op.execute("""
        CREATE TABLE fixes (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            scan_id         BIGINT REFERENCES scans(id),
            rule_id         BIGINT REFERENCES rules(id),
            removed         JSONB,
            added           JSONB,
            rationale       TEXT,
            before          TEXT,
            after           TEXT,
            applied         BOOLEAN NOT NULL DEFAULT false,
            verify_scan_id  BIGINT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX fixes_project_idx ON fixes (project_id)")

    # Global library (not per-project) — populated by hand in 01-07 from
    # garak/PyRIT/HackAPrompt/etc., licence-checked. Empty after this
    # migration; the tracer's one hard-coded technique (01-01 Task 3) does
    # not read from this table yet.
    op.execute("""
        CREATE TABLE techniques (
            id       TEXT PRIMARY KEY,
            family   TEXT,
            targets  JSONB,
            surfaces JSONB,
            template TEXT,
            turns    JSONB,
            canary   TEXT,
            licence  TEXT,
            source   TEXT
        )
    """)

    # Reporting only (PROJECT.md: flywheel promote/retire/mutate is cut) —
    # which technique beat which rule category on which surface.
    op.execute("""
        CREATE TABLE technique_stats (
            id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            technique_id  TEXT NOT NULL,
            rule_category TEXT NOT NULL,
            surface_kind  TEXT NOT NULL,
            attempts      INTEGER NOT NULL DEFAULT 0,
            hits          INTEGER NOT NULL DEFAULT 0,
            UNIQUE (technique_id, rule_category, surface_kind)
        )
    """)


def downgrade() -> None:
    for table in (
        "technique_stats",
        "techniques",
        "fixes",
        "gaps",
        "scan_events",
        "attack_runs",
        "scans",
        "surfaces",
        "questions",
        "rules",
        "prompt_versions",
        "projects",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
