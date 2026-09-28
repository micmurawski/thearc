"""Stable domain contracts for episodic agent adaptation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from thearc.learning.sessions.models import Event, Session


class EpisodeOutcome(BaseModel):
    status: Literal["success", "failure", "mixed", "unknown"] = "unknown"
    score: float | None = None
    signals: dict[str, Any] = Field(default_factory=dict)


class Episode(BaseModel):
    """A learning unit assembled from one or more provider sessions/runs."""

    id: str
    sessions: list[Session] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    task: str | None = None
    tags: list[str] = Field(default_factory=list)
    workspace: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    outcome: EpisodeOutcome = Field(default_factory=EpisodeOutcome)
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EpisodeQuery(BaseModel):
    """Provider-neutral selection contract; adapters may support subsets."""

    start_at: datetime | None = None
    end_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    harnesses: list[str] = Field(default_factory=list)
    workspaces: list[str] = Field(default_factory=list)
    outcome_statuses: list[str] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=10000)
    cursor: str | None = None


class ContextSnapshot(BaseModel):
    """Immutable input version supplied to reflection or curation."""

    id: str
    revision: str
    rank_visible: bool = False
    resources: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunCheckpoint(BaseModel):
    run_id: str
    flow_name: str
    flow_version: str
    status: Literal["running", "paused", "completed", "failed"] = "running"
    node: str
    history_revision: int | None = None
    context_revision: str | None = None
    completed_nodes: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime
