"""Runtime configuration, read once from the environment.

Mirrors citedelta.config.Settings (CiteDelta-RAG/packages/citedelta/src/citedelta/config.py)
— same shape, same Supabase/pgBouncer-safe reasoning for the two SQLAlchemy
properties below, because Alembic runs through SQLAlchemy in both projects.
"""

from __future__ import annotations

import ssl
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    ephemeral_grace_seconds: int = 1800
    """PRIV-02: the window between an ephemeral project's completed-scan
    report first being served (`snag.report.mark_report_served`) and
    `snag.report.purge_expired_ephemeral` hard-deleting it. Long enough for
    one real post-scan viewing session (open a few breaks, apply a fix,
    rescan, export) before the "keep nothing" promise is enforced
    automatically, with no client DELETE call required."""

    default_model: str = "qwen/qwen3.8-flash"
    """The final fallback when `accepted_models` is empty. Whenever
    `accepted_models` is non-empty, `_default_model_must_be_accepted` below
    forces this to a member of it — this hardcoded value only matters for
    local/dev runs with no ACCEPTED_MODELS set at all."""

    judge_model: str = "openai/gpt-5.6-luna"
    """The TIER 2 judge (`snag.judge`): the model that scores rules no
    mechanical checker covers, and reviews mechanical breaks over
    descriptive phrases. Deliberately the STRONGEST model on the allowlist
    rather than the cheapest — it is asked semantic questions the target
    model is expected to get wrong, and it runs a handful of batched times
    per scan, not once per attack. Never used against a scan whose target IS
    this model (`snag.judge.judge_model_for`): a model grading its own
    homework is a known bias, not a saving."""

    accepted_models: Annotated[list[str], NoDecode] = []
    """KEY-03: only these OpenRouter models may ever be dispatched to when
    non-empty (`snag.api.deps.validate_model` enforces this server-side;
    `GET /api/models` is the frontend's live source for its model picker).
    Unset/empty means no restriction — local/dev flexibility. Parsed from
    the comma-separated `ACCEPTED_MODELS` env var by the validator below.

    `NoDecode` opts this field out of pydantic-settings' default complex-
    field handling, which tries to `json.loads` a list-typed env value
    before any validator runs — and a plain `a,b,c` string isn't JSON, so
    it would raise `SettingsError` before `_split_accepted_models` ever saw
    it."""

    @field_validator("accepted_models", mode="before")
    @classmethod
    def _split_accepted_models(cls, value: Any) -> Any:
        """`ACCEPTED_MODELS=a,b,c` arrives as one comma-separated string
        from the environment; split it here since `NoDecode` (above) turns
        off pydantic-settings' own (JSON-only) complex-field parsing."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _default_model_must_be_accepted(self) -> Settings:
        """KEY-03: the default model must never be a value `validate_model`
        would itself reject. Falls back to `accepted_models[0]` rather than
        raising, so a stale `DEFAULT_MODEL` env var can't crash startup —
        the allowlist wins the disagreement silently and predictably. The
        judge is dispatched to like any other model, so it is held to the
        same allowlist by the same rule."""
        if self.accepted_models and self.default_model not in self.accepted_models:
            self.default_model = self.accepted_models[0]
        if self.accepted_models and self.judge_model not in self.accepted_models:
            self.judge_model = self.accepted_models[0]
        return self

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
