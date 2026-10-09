"""Reflection: a reflector judges a MetaAgent's files against evidence and returns schema-shaped items.

``reflect`` attaches the evidence to the prompt; ``handoff`` installs the session (or a part of
it) into the reflector's harness and forks it, so the reflector starts from that history. Either
way the reflector reads the rating-free agent files from a workspace of its own (it never sees
the journal) and hands in its items through the ``submit_reflection`` tool, which checks them
against the schema and the files and fails with the reasons until they are right. The reflector
works that out in its own loop, within one turn.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thearc.resources.agent import MetaAgent
from thearc.resources.reflection import ReflectionItem, ReflectionSchema
from thearc.sessions.session import EventSequence, Session

from .runtimes import RUNTIMES, TOOL, ReflectionError, Runtime
from .submission import Submission

INSTRUCTIONS = """You are reviewing an AI agent's configuration — its instructions, skills, hooks and other
files — against evidence of how the agent behaved. Judge which parts of the configuration helped,
hurt, or made no difference, and why. Ground every judgment in the evidence; do not invent events.
You may only read files; do not modify anything."""

SUBMIT = f"""Read the files in `{{workspace}}`. Cite lines relative to that folder as `path#Lx` or
`path#Lx-Ly` (1-based, inclusive); a citation counts for every section its lines touch, so cite
exactly the lines you judge. Hand in your reflection with the `{TOOL}` tool, complete, in one
call (a reflection without ratings is valid). If it reports problems, fix them and submit the
complete reflection again. You are done when it accepts."""


@dataclass(frozen=True)
class Reflection(Sequence[ReflectionItem]):
    """A reflector's accepted output, exactly as the schema declares it, and what it judged.

    ``output`` is the submitted object as JSON; ``items`` are the ratings found in it (see
    ``ReflectionSchema``), and iterating yields them. ``agent.add_reflection`` resolves each item's
    reference against the judged revision and records the ratings. ``extra`` holds the caller's
    fields for every recorded entry; ``attempts`` counts the reflector's calls to the submit tool.
    """

    id: str
    output: Any
    items: tuple[ReflectionItem, ...]
    revision: str
    at: datetime
    reflector: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1
    notes: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def item_id(self, item: ReflectionItem) -> str:
        """The item's stable ID: the reflection's ID and the item's place in the output, e.g. ``…#/ratings/2``."""
        return f"{self.id}#{item.path}"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form; ``output`` is the reflector's answer exactly as the schema declares it."""
        return {
            "id": self.id,
            "revision": self.revision,
            "at": self.at.isoformat(),
            "reflector": self.reflector,
            "evidence": self.evidence,
            "attempts": self.attempts,
            "notes": list(self.notes),
            "extra": self.extra,
            "output": self.output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], schema: ReflectionSchema) -> Reflection:
        """The reflection ``to_dict`` gave, read with the schema it was made with (e.g. to replay it)."""
        output, items, _ = schema.validate(data["output"])
        return cls(
            id=data["id"],
            output=output,
            items=tuple(items),
            revision=data["revision"],
            at=datetime.fromisoformat(data["at"]),
            reflector=data["reflector"],
            evidence=data.get("evidence", {}),
            attempts=data.get("attempts", 1),
            notes=tuple(data.get("notes", ())),
            extra=data.get("extra", {}),
        )

    def to_markdown(self, *, level: int = 2) -> str:
        """The reflector's output as Markdown: its fields as sections, ratings as a table (see ``markdown``)."""
        from .markdown import to_markdown

        return to_markdown(self.output, level=level)

    def dump_items_to_json(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str, schema: ReflectionSchema) -> Reflection:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")), schema)

    def __getitem__(self, index: Any) -> Any:
        return self.items[index]

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[ReflectionItem]:
        return iter(self.items)


class Reflector:
    """A reflector: a harness and a model.

    reflector = Reflector("codex", model="gpt-5.5")
    reflection = reflector.handoff(PROMPT, session.trajectories[0], agent, schema)
    agent.add_reflection(reflection)
    """

    def __init__(
        self,
        harness: str = "codex",
        *,
        model: str | None = None,
        effort: str | None = None,
        timeout: float = 900,
        home: str | Path | None = None,
        runtime: Runtime | None = None,
    ):
        if runtime is None:
            if harness not in RUNTIMES:
                raise ValueError(f"no reflector runtime for {harness!r}; available: {', '.join(RUNTIMES)}")
            runtime = RUNTIMES[harness](model=model, effort=effort, timeout=timeout)
        self.runtime, self.home = runtime, home

    @property
    def harness(self) -> str:
        return self.runtime.harness

    def reflect(
        self, prompt: str, agent: MetaAgent, schema: ReflectionSchema, *, evidence: str | None = None, **extra: Any
    ) -> Reflection:
        """Reflect on evidence given in the prompt (or as ``evidence`` text appended to it)."""
        task = prompt if evidence is None else f"{prompt}\n\n## Evidence\n\n{evidence}"
        return self._run(task, agent, schema, fork=None, evidence={"attached": evidence is not None}, extra=extra)

    def handoff(
        self, prompt: str, history: EventSequence, agent: MetaAgent, schema: ReflectionSchema, **extra: Any
    ) -> Reflection:
        """Reflect from inside ``history`` (a session, trajectory, or session view), forked in the reflector's harness.

        The history is exported best-effort and installed as a copy; what the export had to
        approximate is listed in ``Reflection.notes``.
        """
        if not self.runtime.can_fork:
            raise ReflectionError("capability", f"{self.harness} cannot continue a session; use reflect()")
        installed = history.install(self.harness, home=self.home, overwrite=True, strict=False)
        session = history if isinstance(history, Session) else getattr(history, "session", None)
        evidence = {
            "handoff": installed.session_id,
            "events": len(history.selected_events),
            **({"session": session.id, "native_session": session.metadata.native_id} if session else {}),
        }
        return self._run(
            prompt, agent, schema, fork=installed, evidence=evidence, extra=extra, notes=installed.transformations
        )

    def _run(
        self,
        task: str,
        agent: MetaAgent,
        schema: ReflectionSchema,
        *,
        fork: Any,
        evidence: dict[str, Any],
        extra: dict[str, Any],
        notes: tuple[str, ...] = (),
    ) -> Reflection:
        files = agent.files()
        submission = Submission(files, schema)
        with tempfile.TemporaryDirectory(prefix="thearc-reflection-") as directory:
            workspace = files.write(Path(directory) / "agent")
            with submission.serve() as tool, self.runtime.conversation(workspace, tool, fork=fork) as conversation:
                conversation.send(
                    f"{INSTRUCTIONS}\n\n## Task\n\n{task}\n\n## Answer\n\n" + SUBMIT.format(workspace=workspace)
                )
        if submission.items is None:
            raise ReflectionError(
                "no_submission", f"The reflector ended without an accepted {TOOL} call ({submission.attempts} rejected)"
            )
        reflector = {"harness": self.harness, "model": self.runtime.model, "session": conversation.id}
        return Reflection(
            id="reflection_" + uuid.uuid4().hex,
            output=submission.output,
            items=tuple(submission.items),
            revision=agent.revision,
            at=datetime.now(UTC),
            reflector=reflector,
            evidence=evidence,
            attempts=submission.attempts,
            notes=tuple(notes),
            extra=extra,
        )
