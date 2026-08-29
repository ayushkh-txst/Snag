"""GET /api/models: the frontend's live source for its model picker,
sourced from the same `ACCEPTED_MODELS` allowlist `deps.validate_model`
enforces server-side — never a static fixture (KEY-03). Auto-registered by
`snag.api.app._include_routers`, same as every other router module here.
"""

from __future__ import annotations

from fastapi import APIRouter

from snag.config import get_settings

router = APIRouter()


@router.get("/models")
async def list_models() -> dict[str, list[str]]:
    return {"models": get_settings().accepted_models}
