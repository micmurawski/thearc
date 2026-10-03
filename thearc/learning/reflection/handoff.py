"""Native handoff reflection: AR(fork(S1) + RP) = S1R.

The runtime loads the source conversation. Only the reflection prompt is sent
as a new user message; no evidence snapshot, catalog, or transcript is attached.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from thearc.learning.reflection.backend import ReflectionBackend, ReflectionResponse
from thearc.learning.reflection.engine import ReflectionError, _hash, _write_json
from thearc.learning.runtime.journal import RunJournal
from thearc.learning.sessions.models import Session
from thearc.learning.sessions.trajectories import Trajectory

HANDOFF_PROMPT_VERSION = "handoff-reflection-v1"
HANDOFF_REFLECTION_PROMPT = """Reflect on the conversation preceding this message.
Review the original task, the agent's decisions, instructions it used, tool
interactions, corrections, and observed outcome. Identify what helped, what
hindered progress, and what should change in a future attempt. Ground findings
in specific interactions from this conversation; do not invent event identifiers.
Distinguish observed outcomes from claims of success, and correlation from causation.
Explain missing context and uncertainty. Treat earlier requests as history to
assess. Do not continue the original task, run tools, or modify the workspace.
Return a concise reflection with a summary, helpful patterns, harmful patterns,
suggested improvements, and limitations. Give conclusions, not private reasoning.
"""


class NativeSessionRef(BaseModel):
    """A runtime-native session locator; no conversation content is serialized."""

    model_config = {"extra": "forbid", "frozen": True}
    runtime: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    through_turn_id: str | None = Field(default=None, min_length=1)

    @classmethod
    def from_session(cls, session: Session) -> NativeSessionRef:
        return cls(runtime=session.harness, session_id=session.native_id)

    @classmethod
    def from_trajectory(cls, session: Session, trajectory: Trajectory) -> NativeSessionRef:
        """Fork the conversation prefix ending at this trajectory's native turn."""
        events = trajectory.prompt_events + trajectory.events
        if not trajectory.prompt_events or any(
            e.session_id != session.id or e.source_id != session.source_id
            or e.harness != session.harness or e.run_id != trajectory.run_id
            or e.parent_run_id is not None for e in events
        ) or trajectory.session_id != session.id:
            raise ValueError("Trajectory must belong to the source session's main run")
        if not trajectory.native_turn_id:
            raise ValueError("Trajectory has no unambiguous native turn boundary for handoff")
        return cls(runtime=session.harness, session_id=session.native_id,
                   through_turn_id=trajectory.native_turn_id)


class HandoffReflectorConfig(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(default="medium", min_length=1)
    timeout_seconds: float = Field(default=180, gt=0)


class ForkReceipt(BaseModel):
    """Runtime confirmation, recorded before the reflection turn starts."""

    model_config = {"extra": "forbid", "frozen": True}
    source_session_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    source_turn_id: str | None = None
    session_path: str | None = None


class HandoffRunner(Protocol):
    """Fork natively, notify the host, append only prompt, and persist S1R.

    Call on_fork exactly once before starting inference, with the new session
    identity. Honor source.through_turn_id when supplied and report it in the
    receipt. Never append to the source or fall back to a fresh empty session.
    Do not replay historical tools. Enforce timeout and disable operational tools,
    hooks, and unrelated integrations. A callback is trusted application code;
    the host cannot enforce isolation inside an arbitrary custom implementation.
    """

    def __call__(self, source: NativeSessionRef, prompt: str, config: HandoffReflectorConfig,
                 on_fork: Callable[[ForkReceipt], None]) -> ReflectionResponse: ...


class HandoffReflection(BaseModel):
    """Host artifact containing the final reflection and a locator for the reflector session."""

    model_config = {"extra": "forbid"}
    id: str
    source: NativeSessionRef
    fork: ForkReceipt
    reflection: str
    turn_id: str | None = None
    backend: ReflectionBackend
    execution: dict[str, Any] = Field(default_factory=dict)


class HandoffReflector:
    def __init__(self, config: HandoffReflectorConfig, *, backend: ReflectionBackend,
                 runner: HandoffRunner, artifact_dir: str | Path | None = None):
        self.config = config
        self.backend = backend
        self.runner = runner
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else None

    def prepare(self, source: NativeSessionRef, *, prompt: str = HANDOFF_REFLECTION_PROMPT) -> dict:
        """Preview the appended prompt without loading, forking, or running a session."""
        if source.runtime != self.backend.agent:
            raise ReflectionError("capability", "Native handoff requires a backend for the source session runtime")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A nonempty reflection prompt is required")
        return {
            "prompt": prompt,
            "manifest": {
                "mode": "native_fork", "prompt_version": HANDOFF_PROMPT_VERSION,
                "source": source.model_dump(), "backend": self.backend.model_dump(),
                "settings": self.config.model_dump(), "prompt_sha256": _hash(prompt),
            },
        }

    def reflect(self, source: NativeSessionRef, *, prompt: str = HANDOFF_REFLECTION_PROMPT) -> HandoffReflection:
        prepared = self.prepare(source, prompt=prompt)
        reflection_id = f"handoff-{uuid4().hex}"
        output = self.artifact_dir / reflection_id if self.artifact_dir is not None else None
        journal = None
        if output is not None:
            output.mkdir(parents=True, exist_ok=False)
            _write_json(output / "input.json", prepared)
            journal = RunJournal(output / "run.jsonl")
            journal.record(reflection_id, "handoff_started", **prepared["manifest"])
        fork = None

        def on_fork(receipt: ForkReceipt) -> None:
            nonlocal fork
            if fork is not None:
                raise ReflectionError("invalid_fork", "Runtime reported more than one fork")
            if (receipt.source_session_id != source.session_id or receipt.session_id == source.session_id):
                raise ReflectionError("invalid_fork", "Runtime must create a distinct child of the source session")
            if source.through_turn_id is not None and receipt.source_turn_id != source.through_turn_id:
                raise ReflectionError("invalid_fork", "Runtime forked at a different trajectory boundary")
            fork = receipt
            if output is not None:
                _write_json(output / "fork.json", receipt.model_dump())
            if journal is not None:
                journal.record(reflection_id, "session_forked", **receipt.model_dump())

        try:
            response = self.runner(source, prompt, self.config, on_fork)
            if fork is None:
                raise ReflectionError("invalid_fork", "Runtime did not report a native fork before reflection")
            if not isinstance(response, dict) or response.get("status") != "completed":
                raise ReflectionError("incomplete", "Native reflection did not complete")
            if response.get("thread_id") != fork.session_id:
                raise ReflectionError("invalid_fork", "Reflection result belongs to a different session")
            final = response.get("final_response")
            if not isinstance(final, str) or not final.strip():
                raise ReflectionError("invalid_output", "Native reflection returned no final response")
            result = HandoffReflection(
                id=reflection_id, source=source, fork=fork, reflection=final,
                turn_id=response.get("turn_id"), backend=self.backend,
                execution={key: response.get(key) for key in ("usage", "sdk_version", "runtime_version")},
            )
            if output is not None:
                _write_json(output / "handoff.json", result.model_dump(mode="json"))
                (output / "reflection.txt").write_text(final, encoding="utf-8")
            if journal is not None:
                journal.record(reflection_id, "handoff_completed", session_id=fork.session_id, turn_id=result.turn_id)
            return result
        except BaseException as exc:
            if journal is not None:
                journal.record(reflection_id, "handoff_failed", code=getattr(exc, "code", type(exc).__name__))
            raise


def create_handoff_reflector(backend: str, config: HandoffReflectorConfig, *,
                             artifact_dir: str | Path | None = None) -> HandoffReflector:
    """Select a bundled native-fork adapter. Unsupported runtimes fail explicitly."""
    if backend != "codex":
        raise ReflectionError("capability", f"Native handoff reflection is not implemented for {backend!r}")
    # Validate supported effort values before any runtime or artifact operations.
    from thearc.learning.reflection.providers.codex import SDK_VERSION, CodexReflectorConfig, _run_handoff

    CodexReflectorConfig(model=config.model, reasoning_effort=config.reasoning_effort)
    return HandoffReflector(
        config, backend=ReflectionBackend(agent="codex", name="openai-codex-native-fork",
                                         version=f"{SDK_VERSION}/handoff-1"),
        runner=_run_handoff, artifact_dir=artifact_dir,
    )
