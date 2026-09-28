"""Minimal execution contract, separate from session formats and config installers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from pydantic import BaseModel, Field

from thearc.learning.sessions.models import Harness

if TYPE_CHECKING:
    from thearc.learning.reflection.engine import ReflectorConfig


class ReflectionBackend(BaseModel):
    """Stable execution identity; bump version when runtime behavior/settings change."""

    model_config = {"frozen": True, "extra": "forbid"}
    agent: Harness
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class _RequiredResponse(TypedDict):
    status: str
    final_response: str


class ReflectionResponse(_RequiredResponse, total=False):
    thread_id: str | None
    turn_id: str | None
    usage: dict[str, Any] | None
    sdk_version: str | None
    runtime_version: str | None


class ReflectionRunner(Protocol):
    """Execute one fresh inspection turn after receiving the prompt.

    Implementations must isolate instructions and disable operational tools,
    hooks, skills, and unrelated integrations; a prompt alone is not isolation.
    Honor timeout/model settings, never resume evaluated sessions, and never
    replay evidence. Return schema-conforming JSON text, not Markdown fences.
    Optional envelope keys: thread_id, turn_id, usage, sdk_version.
    Raise ReflectionError for classified failures; only 'transient' is retried.
    The host validates findings/citations but cannot sandbox an arbitrary callback.
    """

    def __call__(self, prompt: str, schema: dict[str, Any], config: ReflectorConfig) -> ReflectionResponse: ...
