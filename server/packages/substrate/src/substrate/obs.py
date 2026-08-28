"""Structured logging with a run ID threaded through everything."""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

_run_id: ContextVar[str] = ContextVar("run_id", default="-")


def new_run_id() -> str:
    """Start a new logical run. Every log line after this carries the id."""
    rid = uuid.uuid4().hex[:12]
    _run_id.set(rid)
    return rid


def _inject_run_id(
    _logger: object, _method: str, event: MutableMapping[str, Any]
) -> dict[str, Any]:
    event["run_id"] = _run_id.get()
    return dict(event)


def configure_logging(level: str = "info", *, json_output: bool = False) -> None:
    """Console renderer for humans, JSON for anything that gets grepped."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_run_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )
