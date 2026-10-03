"""Provider-neutral history contracts; IDs are scoped to a configured source."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Harness = Literal["pi", "codex", "claude", "antigravity"]


class SourceConfig(BaseModel):
    id: str = Field(min_length=1)
    harness: Harness
    root: Path
    format: Literal["native", "dataclaw"] = "native"

    @field_validator("root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()


class SourceReference(BaseModel):
    path: Path
    generation: int
    byte_offset: int
    byte_length: int
    line: int
    json_pointer: str = ""


class Session(BaseModel):
    """Provider-neutral conversation/session projection."""

    id: str
    source_id: str
    harness: Harness
    native_id: str
    title: str | None = None
    cwd: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    event_count: int = 0
    run_count: int = 0
    parent_session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Run(BaseModel):
    """An execution context within a session, including delegated agents."""

    id: str
    session_id: str
    source_id: str
    harness: Harness
    parent_run_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    event_count: int = 0


class Event(BaseModel):
    id: str
    source_id: str
    harness: Harness
    session_id: str
    session_native_id: str | None = None
    run_id: str
    native_id: str | None = None
    parent_native_id: str | None = None
    timestamp: str | None = None
    kind: str
    role: str | None = None
    text: str = ""
    tool_name: str | None = None
    action_kind: str | None = None
    call_id: str | None = None
    status: str | None = None
    arguments: Any = None
    cwd: str | None = None
    parent_run_id: str | None = None
    native_type: str | None = None
    reference: SourceReference
    raw: dict[str, Any] = Field(default_factory=dict)
    raw_pointer: str = ""


class SearchFilters(BaseModel):
    started_before: datetime | None = None
    started_after: datetime | None = None
    source_ids: list[str] = Field(default_factory=list)
    harnesses: list[Harness] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    action_kinds: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)

    @field_validator("started_before", "started_after")
    @classmethod
    def normalize_cutoff(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("Session date filters require timezone-aware datetimes")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_date_range(self) -> SearchFilters:
        if (self.started_before is not None and self.started_after is not None
                and self.started_after >= self.started_before):
            raise ValueError("started_after must be earlier than started_before")
        return self


class SearchQuery(BaseModel):
    text: str = Field(min_length=1)
    mode: Literal["literal", "lexical"] = "literal"
    case_sensitive: bool = True
    filters: SearchFilters = Field(default_factory=SearchFilters)
    include_internal: bool = False
    limit: int = Field(default=20, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class SearchHit(BaseModel):
    event: Event
    snippet: str
    match_ranges: list[tuple[int, int]] = Field(default_factory=list)
    score: float | None = None
    match_reason: str
    source_available: bool


class SearchPage(BaseModel):
    hits: list[SearchHit]
    has_more: bool
    next_offset: int | None
    index_revision: int
    warnings: list[str] = Field(default_factory=list)


class SyncReport(BaseModel):
    artifacts: int = 0
    events_added: int = 0
    records_skipped: int = 0
    partial_files: int = 0
    warnings: list[str] = Field(default_factory=list)


class RunTrace(BaseModel):
    run_id: str
    parent_run_id: str | None = None
    children: list[str] = Field(default_factory=list)
    descendants: list[str] = Field(default_factory=list)
    delegation_events: list[Event] = Field(default_factory=list)
    relationship_basis: str = "transcript association; execution linkage may be unavailable"
