"""Typed boundary for the queue. No dicts cross a module line."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class JobSpec(BaseModel):
    """What a producer submits."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    queue: str = "default"
    idempotency_key: str | None = None
    priority: int = 0
    max_attempts: int = Field(default=5, ge=1)
    available_at: datetime | None = None  # None = runnable immediately


class ClaimedJob(BaseModel):
    """What a worker holds. `lease_epoch` is the proof of ownership."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int
    queue: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_epoch: int

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempts)


class QueueStats(BaseModel):
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    dead: int = 0

    @property
    def outstanding(self) -> int:
        return self.pending + self.running
