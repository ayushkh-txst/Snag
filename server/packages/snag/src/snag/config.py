"""Runtime configuration, read once from the environment.

Mirrors citedelta.config.Settings (CiteDelta-RAG/packages/citedelta/src/citedelta/config.py)
— same shape, same Supabase/pgBouncer-safe reasoning for the two SQLAlchemy
properties below, because Alembic runs through SQLAlchemy in both projects.
"""

from __future__ import annotations

import ssl
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every knob the app has. Anything not here is a hard-coded constant."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://snag:snag@localhost:5432/snag"
    log_level: str = "info"

    llm_provider: str = "openrouter"
    """'openrouter' is the only provider Snag ships (substrate.llm.factory
    also knows 'anthropic', vendored along with everything else, but Snag's
    BYOK story — one key, every model via OpenRouter — never selects it)."""

    openrouter_api_key: str = ""
    """The optional OWNER key: set, it enables key-free scans (dev and the
    seeded examples). Never logged, never returned in a response body — see
    T-01-02. Per-request BYOK (`X-OpenRouter-Key`) is the seam plan 01-02
    adds in `snag.api.deps.get_completions`; this plan wires the owner-key
    path only."""

    default_model: str = "openai/gpt-4o-mini"

    @property
    def sqlalchemy_url(self) -> str:
        """Alembic runs through SQLAlchemy, which wants the driver named in
        the URL, and drops `sslmode` (asyncpg's dialect forwards unknown
        query params straight to `asyncpg.connect()`, which has no such
        kwarg) — see `sqlalchemy_connect_args` for how TLS is actually
        requested. Identical reasoning to citedelta.config.Settings."""
        url = self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        parts = urlsplit(url)
        kept = [(k, v) for k, v in parse_qsl(parts.query) if k != "sslmode"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))

    @property
    def sqlalchemy_connect_args(self) -> dict[str, Any]:
        """Extra asyncpg-level connect kwargs the URL alone can't carry.

        `statement_cache_size=0` unconditionally, for the same
        transaction-pooler reason as citedelta.config.Settings (a
        statement/transaction-mode PgBouncer — Supabase's pooler, among
        others — can hand one physical connection to different logical
        clients between statements, and asyncpg's default prepared-statement
        cache then collides across them).

        `ssl` only when `database_url` asked for it via `sslmode`, so a
        local direct connection isn't forced into a handshake it can't do.
        """
        args: dict[str, Any] = {"statement_cache_size": 0}
        sslmode = dict(parse_qsl(urlsplit(self.database_url).query)).get("sslmode")
        if sslmode and sslmode != "disable":
            tls = ssl.create_default_context()
            tls.check_hostname = False
            tls.verify_mode = ssl.CERT_NONE
            args["ssl"] = tls
        return args


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so config is parsed once per process, not once per call site."""
    return Settings()
