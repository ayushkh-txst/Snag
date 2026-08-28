"""The HTTP surface: FastAPI factory + app.state DI, mirroring CiteDelta's
`create_app()`/`build_state()`/`ctx()` pattern
(CiteDelta-RAG/packages/citedelta/src/citedelta/api/app.py).
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from snag.api import routers as routers_pkg
from snag.api.state import AppState, build_state, close_state
from snag.config import get_settings
from substrate.llm import CompletionError

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.ctx = await build_state(settings)
    log.info("api.ready")
    try:
        yield
    finally:
        await close_state(app.state.ctx)


def ctx(request: Request) -> AppState:
    return cast(AppState, request.app.state.ctx)


def _include_routers(app: FastAPI) -> None:
    """Import every module under snag.api.routers and register its `router`.

    A new router file is enough to wire a new endpoint set — no separate
    registration list to keep in sync (and forget).
    """
    for module_info in pkgutil.iter_modules(routers_pkg.__path__):
        module = importlib.import_module(f"{routers_pkg.__name__}.{module_info.name}")
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router, prefix="/api")


def _dist_dir() -> Path:
    """Where the built SPA lives. Both `entrypoint.sh` and local dev run
    `snag serve` from `server/` (`cd server && uv run snag serve`), so the
    repo's `dist/` is always one level up from the process's cwd — true in
    the Docker image too, where `dist/` is copied to that same relative
    place (see Dockerfile)."""
    return Path.cwd().parent / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="Snag", lifespan=lifespan)
    _include_routers(app)

    @app.exception_handler(CompletionError)
    async def completion_error_handler(request: Request, exc: CompletionError) -> Response:
        # A provider outage is neither a bug nor a "checker failed" result —
        # a distinct 502 keeps scan failure modes separable, same discipline
        # as CiteDelta's own handler.
        log.error("api.provider_unavailable", error=str(exc))
        return Response(
            status_code=502,
            media_type="application/json",
            content='{"detail": "model provider unavailable"}',
        )

    dist = _dist_dir()
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")

    return app
