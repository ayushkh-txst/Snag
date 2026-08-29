"""Snag is link-only: nothing it serves belongs in a search index."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx

from substrate.llm import FakeCompletions

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]


async def test_robots_txt_disallows_everything(client_factory: ClientFactory) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.get("/robots.txt")
    assert res.status_code == 200
    assert res.text == "User-agent: *\nDisallow: /\n"


async def test_every_response_carries_the_noindex_header(client_factory: ClientFactory) -> None:
    """Including the API, not just the document — a report URL is as
    linkable as the page that renders it."""
    async with client_factory(FakeCompletions()) as client:
        for path in ("/robots.txt", "/api/healthz", "/api/nope"):
            res = await client.get(path)
            assert res.headers["x-robots-tag"] == "noindex, nofollow", path
