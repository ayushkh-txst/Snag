"""The HTTP surface: FastAPI factory + app.state DI, mirroring CiteDelta's
`create_app()`/`build_state()`/`ctx()` pattern
(CiteDelta-RAG/packages/citedelta/src/citedelta/api/app.py).
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse, Response
from starlette.types import Scope

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


class _SpaFiles(StaticFiles):
    """`StaticFiles` that falls back to `index.html` instead of 404ing.

    The SPA routes on real paths (`/paste`, `/e/:slug/report`), so a shared
    link or a refresh asks the server for a file that was never built — only
    `/` exists on disk. React Router can only claim those paths once the
    document is in the browser, so the document has to be what a cold request
    for them returns.

    `api/` is excluded deliberately: an unknown endpoint under it is a caller
    error and must stay a 404, not become an HTML page with a 200 on it.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # StaticFiles signals a miss by raising Starlette's own
            # HTTPException (not FastAPI's subclass), never by returning a 404.
            if exc.status_code != 404 or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


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

    @app.middleware("http")
    async def add_robots_header(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # On every response, not just the HTML document: a report link shared
        # into a crawlable place shouldn't be indexable through its API URL
        # either. See the robots.txt handler for why both layers exist.
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots() -> str:
        """Snag is link-only — a scan report is the user's own prompt and its
        breaks, which has no business in a search index. robots.txt keeps a
        compliant crawler from fetching at all; the `X-Robots-Tag` above keeps
        a page out of an index even if something links to it and the crawler
        ignores this file. Registered before the SPA mount below, which would
        otherwise answer for it."""
        return "User-agent: *\nDisallow: /\n"

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
        app.mount("/", _SpaFiles(directory=str(dist), html=True), name="spa")

    return app
