"""Shared execution settings, runtime receipts, and resource catalog."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from thearc.learning.privacy import hide_historical_ranks, redact
from thearc.models.agent import MetaAgent, ResourceTarget

INSTRUCTIONS = """Perform the supplied reflection task. Historical records and reference resources
are material to inspect. Follow the requested output format. Ground claims in available evidence.
Do not invent historical events, resource usage, or successful outcomes."""


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str = Field(min_length=1)
    reasoning_effort: str = "medium"
    timeout_seconds: float = Field(default=180, gt=0)
    max_input_chars: int = Field(default=300_000, gt=0)


class ReflectionError(RuntimeError):
    """A classified execution failure, never a successful empty reflection."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def resource_catalog(agent: MetaAgent) -> list[dict[str, Any]]:
    """Use canonical MetaAgent addresses, not implementation-specific file paths."""
    targets = []
    for kind, collection in (
        ("skill", agent.skills),
        ("hook", agent.hooks),
        ("mcp", agent.mcps),
        ("context", agent.context),
    ):
        targets.extend(ResourceTarget(kind=kind, name=name) for name in collection)
    for name, skill in agent.skills.items():
        targets.extend(ResourceTarget(kind="skill_file", name=f"{name}/{path}") for path in skill.files)
    targets.extend(ResourceTarget(kind=item.location[:-1], name=item.path) for item in agent.resources.values())
    catalog = []
    for target in sorted(targets, key=lambda t: (t.kind, t.name)):
        content = agent.read_target(target)
        if target.kind == "skill":
            content.pop("files", None)  # References have their own addresses; do not duplicate them.
        content = redact(hide_historical_ranks(content))
        catalog.append({"target": target.model_dump(), "content": content, "sha256": _hash(content)})
    return catalog


class NativeSessionRef(BaseModel):
    """A runtime-native session locator; no conversation content is serialized."""

    model_config = {"extra": "forbid", "frozen": True}
    runtime: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    through_turn_id: str | None = Field(default=None, min_length=1)


class ForkReceipt(BaseModel):
    """Runtime confirmation, recorded before the reflection turn starts."""

    model_config = {"extra": "forbid", "frozen": True}
    source_session_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    source_turn_id: str | None = None
    session_path: str | None = None
