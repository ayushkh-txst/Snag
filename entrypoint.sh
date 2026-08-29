#!/usr/bin/env bash
# Runs on every container start. Postgres is the source of truth for schema
# state, so migrations run before the API ever accepts a request.
set -euo pipefail

uv run alembic upgrade head

# 01-15: seed the six read-only examples so they're browsable with no key
# right after a fresh deploy. Idempotent (skips slugs already seeded, at
# zero additional spend — T-15-02) and non-fatal: a missing/invalid
# OPENROUTER_API_KEY must never keep the API itself from starting.
uv run snag seed || echo "snag seed: skipped (see above for why)"

# Render injects $PORT; a bare local `docker run` won't set one, so fall
# back to 8000 (also what EXPOSE in the Dockerfile documents).
exec uv run snag serve --host 0.0.0.0 --port "${PORT:-8000}"
