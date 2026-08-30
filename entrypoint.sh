#!/usr/bin/env bash
# Runs on every container start. Postgres is the source of truth for schema
# state, so migrations run before the API ever accepts a request.
set -euo pipefail

uv run alembic upgrade head

# Seed the six read-only examples so they're browsable with no key right
# after a fresh deploy. Idempotent (skips slugs already seeded, at zero
# additional spend) and non-fatal: a missing/invalid OPENROUTER_API_KEY must
# never keep the API itself from starting.
#
# Backgrounded, because against an empty database this is minutes of real
# model calls — long enough that Render stops waiting for the port to open
# and kills the deploy before `snag serve` is ever reached. The examples
# appear in the gallery as they land; the API is up the whole time.
uv run snag seed || echo "snag seed: skipped (see above for why)" &

# A scan started from the UI is a queued job, not work the request does
# itself, so something has to claim it. Nothing did: every scan enqueued
# against the deployed service sat `pending` with `attempts = 0` forever
# while the UI showed "Queuing attacks", and the seeded examples hid it
# because `snag seed` runs the pipeline in-process and never touches the
# queue. One worker in the same container rather than a second Render
# service, which the free plan does not stretch to.
#
# Funded by the server's own OPENROUTER_API_KEY: a durable job outlives the
# request that enqueued it, and a caller's key is deliberately never
# persisted (see `run_scan_worker`).
uv run snag work --forever --concurrency 2 || echo "snag work: exited (see above)" &

# Render injects $PORT; a bare local `docker run` won't set one, so fall
# back to 8000 (also what EXPOSE in the Dockerfile documents).
exec uv run snag serve --host 0.0.0.0 --port "${PORT:-8000}"
