"""The SPA routes on real paths, so the server has to answer for them.

Driven against `_SpaFiles` on a temporary directory rather than the app's
own `dist/`, which only exists after an `npm run build` the test suite
doesn't perform.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from snag.api.app import _SpaFiles


@pytest.fixture
def spa_client(tmp_path: Path) -> httpx.AsyncClient:
    (tmp_path / "index.html").write_text("<!doctype html><title>Snag</title>")
    (tmp_path / "asset.js").write_text("console.log(1)")
    app = FastAPI()
    app.mount("/", _SpaFiles(directory=str(tmp_path), html=True), name="spa")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.mark.parametrize("path", ["/", "/paste", "/examples", "/e/demo/report/42"])
async def test_client_routes_serve_the_document(spa_client: httpx.AsyncClient, path: str) -> None:
    async with spa_client as client:
        res = await client.get(path)
    assert res.status_code == 200
    assert "<title>Snag</title>" in res.text


async def test_real_assets_are_still_served(spa_client: httpx.AsyncClient) -> None:
    async with spa_client as client:
        res = await client.get("/asset.js")
    assert res.status_code == 200
    assert res.text == "console.log(1)"


async def test_unknown_api_paths_stay_404(spa_client: httpx.AsyncClient) -> None:
    """An unknown endpoint is a caller error. Answering it with the SPA
    document and a 200 would turn every typo'd request into a silent success."""
    async with spa_client as client:
        res = await client.get("/api/does-not-exist")
    assert res.status_code == 404
