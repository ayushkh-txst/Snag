#!/usr/bin/env bash
# Runs on every container start. Postgres is the source of truth for schema
# state, so migrations run before the API ever accepts a request.
set -euo pipefail

uv run alembic upgrade head

# Render injects $PORT; a bare local `docker run` won't set one, so fall
# back to 8000 (also what EXPOSE in the Dockerfile documents).
exec uv run snag serve --host 0.0.0.0 --port "${PORT:-8000}"
