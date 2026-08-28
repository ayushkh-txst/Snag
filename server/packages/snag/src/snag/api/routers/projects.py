"""POST /api/projects (Paste): a pasted system prompt becomes a project, an
extracted rule set, and the one always-present "chat" surface. GET returns
the bare project row.
"""

from __future__ import annotations

import json
import secrets

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from snag.api.app import ctx
from snag.api.deps import get_completions, require_slug, validate_model
from snag.config import get_settings
from snag.extract import extract_rules
from substrate.llm import Completions

log = structlog.get_logger(__name__)
router = APIRouter()

# T-01-03: length caps enforced before any model call. Pydantic's max_length
# rejects an oversized body with 422 during request parsing — before this
# handler, and therefore before extract_rules ever runs.
MAX_SYSTEM_PROMPT_CHARS = 20_000
MAX_TOOLS_CHARS = 50_000


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(min_length=1, max_length=MAX_SYSTEM_PROMPT_CHARS)
    tools: str | None = Field(default=None, max_length=MAX_TOOLS_CHARS)
    model: str | None = None


class CreateProjectResponse(BaseModel):
    slug: str


@router.post("/projects", response_model=CreateProjectResponse)
async def create_project(
    body: CreateProjectRequest,
    request: Request,
    completions: Completions = Depends(get_completions),  # noqa: B008 - FastAPI DI idiom
) -> CreateProjectResponse:
    settings = get_settings()
    model = body.model or settings.default_model
    validate_model(model)  # KEY-03: before any completions/model call

    tools_obj = None
    if body.tools:
        try:
            tools_obj = json.loads(body.tools)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="tools must be valid JSON") from exc

    extracted = await extract_rules(
        completions, model=model, system=body.system_prompt, tools=body.tools
    )

    # Unguessable by construction (T-01-05) — 9 bytes -> 12 URL-safe base64
    # chars, never interpolated into SQL (T-01-04: bound as a parameter below).
    slug = secrets.token_urlsafe(9)
    state = ctx(request)

    async with state.db.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO projects (id, model, tools_json) VALUES ($1, $2, $3)",
            slug,
            model,
            tools_obj,
        )
        await conn.execute(
            "INSERT INTO prompt_versions (project_id, full_text, tools_json) VALUES ($1, $2, $3)",
            slug,
            body.system_prompt,
            tools_obj,
        )
        for rule in extracted:
            await conn.execute(
                """INSERT INTO rules
                       (project_id, text, category, direction, source_line,
                        checker_type, checker_config, testable, confidence)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                slug,
                rule.text,
                rule.category,
                rule.direction,
                rule.source_line,
                rule.checker_type,
                rule.checker_config,
                rule.checker_type != "none",
                rule.confidence,
            )
        # The chat surface always exists — every pasted prompt reaches the
        # model through a user message. Detecting template-var / tool-param /
        # tool-return surfaces from `tools_obj` is 01-01's follow-on
        # (surfaces.py, architecture-plan.md build-order step 4).
        await conn.execute(
            """INSERT INTO surfaces (project_id, kind, path, source, user_controlled)
               VALUES ($1, 'chat', 'user message', 'chat input', true)""",
            slug,
        )

    log.info("project.created", slug=slug, rules=len(extracted))
    return CreateProjectResponse(slug=slug)


@router.get("/projects/{slug}")
async def get_project(slug: str, request: Request) -> dict[str, object]:
    row = await require_slug(request, slug)
    return {
        "slug": row["id"],
        "name": row["name"],
        "model": row["model"],
        "ephemeral": row["ephemeral"],
        "seeded": row["seeded"],
        "createdAt": row["created_at"].isoformat(),
    }
