# Single deploy image: build the SPA, then serve it from the FastAPI app's
# StaticFiles mount (snag.api.app._dist_dir). One container, one Render
# service — no separate static host to keep in sync (see render.yaml).

# ---------------------------------------------------------------- stage 1: SPA
FROM node:20-slim AS web

WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig*.json ./
COPY public public
COPY src src
RUN npm run build

# ---------------------------------------------------------------- stage 2: API
FROM python:3.12-slim

RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

# Most container platforms (Render included) run as an arbitrary uid unless
# told otherwise — create a real non-root user rather than relying on that.
RUN useradd --create-home --uid 1000 app
USER app
WORKDIR /home/app/snag/server
ENV PATH="/home/app/.local/bin:${PATH}" \
    UV_PROJECT_ENVIRONMENT="/home/app/snag/server/.venv" \
    PYTHONUNBUFFERED=1

COPY --chown=app:app server/pyproject.toml server/uv.lock server/.python-version ./
COPY --chown=app:app server/packages/substrate/pyproject.toml packages/substrate/pyproject.toml
COPY --chown=app:app server/packages/snag/pyproject.toml packages/snag/pyproject.toml

# Split from the full COPY below so dependency install is cached across
# source-only changes — a rebuild after editing app code doesn't re-resolve.
RUN mkdir -p packages/substrate/src/substrate packages/snag/src/snag \
    && uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app server/packages/substrate packages/substrate
COPY --chown=app:app server/packages/snag packages/snag
COPY --chown=app:app server/alembic alembic
COPY --chown=app:app server/alembic.ini alembic.ini
COPY --chown=app:app entrypoint.sh entrypoint.sh

RUN uv sync --frozen --no-dev

# Set only after the build's own `uv sync` calls: `uv run` auto-resyncs by
# default, and would otherwise try to pull the dev dependency group on every
# container start. The venv built above is already correct.
ENV UV_NO_SYNC=1

# snag.api.app._dist_dir resolves to Path.cwd().parent / "dist" — the
# process runs from server/ (WORKDIR above), so dist/ lands one level up,
# i.e. /home/app/snag/dist, matching local dev's repo-root dist/.
COPY --from=web --chown=app:app /web/dist /home/app/snag/dist

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
