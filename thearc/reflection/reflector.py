"""Reflection: a reflector judges an AgentConfig's files against evidence and returns schema-shaped items.

``reflect`` attaches the evidence to the prompt; ``handoff`` installs the session (or a part of
it) into the reflector's harness and forks it, so the reflector starts from that history. Either
way the reflector reads the rating-free agent files from a workspace of its own (it never sees
the journal) and hands in its items through the ``submit_reflection`` tool, which checks them
against the schema and the files and fails with the reasons until they are right. The reflector
works that out in its own loop, within one turn.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thearc.resources.agent import AgentConfig
from thearc.resources.reflection import ReflectionItem, ReflectionSchema
from thearc.sessions.events import records
from thearc.sessions.install import InstalledSession
from thearc.sessions.redact import Redaction, summary
from thearc.sessions.session import EventSequence, Session
from thearc.sessions.threads import Thread

from .mcp import ToolSpec
from .prompts import Prompt, prompt_identity, prompt_text, schema_hash
from .runtimes import RUNTIMES, TOOL, ReflectionError, Runtime
from .submission import Submission

INSTRUCTIONS = """You are evaluating an agent's configuration — instructions, skills, hooks, prompts, and supporting
resources — against evidence from task trajectories. Identify reusable guidance that affected the observed
outcome; do not solve the task again or teach its domain.

Apply the supplied usefulness scale:
- helpful: cited guidance demonstrably contributed to progress, correctness, efficiency, or safe handling;
- harmful: cited guidance demonstrably contributed to an error, wasted effort, unsafe action, or avoidable failure;
- neutral: evidence shows the guidance was encountered or followed but made no material difference.

Ground judgments in the transcript, tool results, tests, or other supplied feedback. Separate observations from
inferences and state uncertainty. A failed task does not by itself show a configuration problem, and missing
evidence is not evidence of harm or neutrality. Do not invent facts, expected behavior, or causal links. Prefer
transferable process lessons; include task-specific details only as evidence. Cite the smallest relevant spans and
leave ratings empty when no causal judgment is supported. You may only read files; do not modify or execute them."""

SUBMIT = f"""Read the files in `{{workspace}}`. Cite lines relative to that folder as `path#Lx` or
`path#Lx-Ly` (1-based, inclusive); a citation counts for every section its lines touch, so cite
exactly the lines you judge. Hand in your reflection with the `{TOOL}` tool, complete, in one
call (a reflection without ratings is valid). If it reports problems, fix them and submit the
complete reflection again. You are done when it accepts."""

# What a harness's session record leaves out, told to the reflector so absence is not read as evidence.
UNRECORDED = {
    "opencode": "OpenCode's session record does not include its system prompt: the project instructions (such as "
    "AGENTS.md) and the skill list it started with are not visible in this history. Their absence from it does "
    "not show that they were not loaded.",
}


def fingerprint(prompt: str | Prompt, schema: ReflectionSchema) -> str:
    """What a reflection was asked: its prompt (the body of a ``Prompt``) and schema, as a short digest. Two reflections
    on the same evidence and revision with the same fingerprint answer the same question (see
    ``Evolution.remembered``)."""
    asked = json.dumps([prompt_text(prompt), schema.json_schema()], sort_keys=True, default=str)
    return hashlib.sha256(asked.encode()).hexdigest()[:16]


def events_digest(history: EventSequence | Sequence[str]) -> str:
    """Which events a reflector was handed, as a short digest of their IDs (or of the IDs given): the same session,
    unchanged, gives the same digest; one that went on since gives another."""
    ids = list(history) if not isinstance(history, EventSequence) else [
        event["id"] for event in records(history.selected_events)]
    return ids_digest(ids)


def ids_digest(ids: Sequence[str]) -> str:
    """The digest ``events_digest`` gives for events with these IDs, in this order."""
    return hashlib.sha256(json.dumps(list(ids)).encode()).hexdigest()[:16]


def estimate_tokens(text: str) -> int:
    """About how many tokens ``text`` is to a model: a quarter of its characters."""
    return len(text) // 4


def _redacted(counts: Counter[str]) -> dict[str, Any]:
    return {"redacted": dict(counts)} if counts else {}


def item_record(item: ReflectionItem) -> dict[str, Any]:
    """An item as a reflection's file lists it, for the session index: where it is, what it cites, its rating."""
    return {"path": item.path, "ref": str(item.ref), "scale": item.scale, "rating": item.rating}


def reflection_mode(data: dict[str, Any]) -> str:
    """How a reflection (as ``to_dict`` gave it) was made; reflections recorded before modes were by their evidence."""
    return data.get("mode") or ("handoff" if (data.get("evidence") or {}).get("handoff") else "attached")


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
    mode: str = "attached"
    trigger: str | None = None
    prompt: dict[str, Any] | None = None
    schema: str | None = None

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
            "mode": self.mode,
            "trigger": self.trigger,
            "prompt": self.prompt,
            "schema": self.schema,
            "items": [item_record(item) for item in self.items],
            "output": self.output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], schema: ReflectionSchema | Any) -> Reflection:
        """The reflection ``to_dict`` gave, read with the schema it was made with (e.g. to replay it)."""
        output, items, _ = ReflectionSchema.of(schema).validate(data["output"])
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
            mode=reflection_mode(data),
            trigger=data.get("trigger"),
            prompt=data.get("prompt"),
            schema=data.get("schema"),
        )

    def to_markdown(self, *, level: int = 2) -> str:
        """The reflector's output as Markdown: its fields as sections, ratings as a table (see ``markdown``)."""
        from .markdown import to_markdown

        return to_markdown(self.output, level=level)

    def dump_items_to_json(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str, schema: ReflectionSchema | Any) -> Reflection:
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
        redaction: Redaction | None = None,
    ):
        """``timeout`` bounds each of the reflector's turns, in seconds. ``redaction`` is what is taken out of the
        evidence before the reflector reads it (default: ``Redaction()``; ``Redaction.off()`` keeps everything)."""
        if runtime is None:
            if harness not in RUNTIMES:
                raise ValueError(f"no reflector runtime for {harness!r}; available: {', '.join(RUNTIMES)}")
            runtime = RUNTIMES[harness](model=model, effort=effort, timeout=timeout)
        self.runtime, self.home = runtime, home
        self.redaction = Redaction() if redaction is None else redaction

    @property
    def harness(self) -> str:
        return self.runtime.harness

    def reflect(
        self, prompt: str | Prompt, agent: AgentConfig, schema: ReflectionSchema | Any, *, evidence: str | None = None,
        **extra: Any
    ) -> Reflection:
        """Reflect on evidence given in the prompt (or as ``evidence`` text appended to it, redacted).

        ``schema`` is a ``ReflectionSchema`` or what one is built from, such as the output model itself."""
        schema = ReflectionSchema.of(schema)
        counts: Counter[str] = Counter()
        if evidence is not None:
            evidence = self.redaction.text(evidence, counts)
        text = prompt_text(prompt)
        task = text if evidence is None else f"{text}\n\n## Evidence\n\n{evidence}"
        attached = {"attached": evidence is not None, "asked": fingerprint(prompt, schema), **_redacted(counts)}
        return self._run(task, agent, schema, fork=None, evidence=attached, extra=extra, prompt=prompt)

    def reflect_with_tools(
        self,
        prompt: str | Prompt,
        agent: AgentConfig,
        schema: ReflectionSchema | Any,
        *,
        mcp_tools: Sequence[ToolSpec],
        evidence: str | None = None,
        **extra: Any,
    ) -> Reflection:
        """Reflect in a fresh session with additional, per-reflection MCP tools."""
        schema = ReflectionSchema.of(schema)
        counts: Counter[str] = Counter()
        if evidence is not None:
            evidence = self.redaction.text(evidence, counts)
        text = prompt_text(prompt)
        task = text if evidence is None else f"{text}\n\n## Evidence\n\n{evidence}"
        attached = {"attached": evidence is not None, "asked": fingerprint(prompt, schema), **_redacted(counts)}
        return self._run(task, agent, schema, fork=None, evidence=attached, extra=extra, mcp_tools=mcp_tools,
                         prompt=prompt)

    def handoff(
        self,
        prompt: str | Prompt,
        history: EventSequence,
        agent: AgentConfig,
        schema: ReflectionSchema | Any,
        *,
        provenance: str | None = None,
        focus: str | None = None,
        judged_from: str | None = None,
        **extra: Any,
    ) -> Reflection:
        """Reflect from inside ``history`` (a session, trajectory, or session view), forked in the reflector's harness.

        The history is exported best-effort and installed as a copy; what the export had to
        approximate is listed in ``Reflection.notes``, and so is what the redaction took out of the copy (counted in
        ``Reflection.evidence["redacted"]``). ``provenance`` is what you know about how the
        session ran that its record does not show — such as which configuration was installed for it;
        the reflector gets it, with what the harness's record leaves out, under "How the session ran".
        ``focus`` points the reflector at parts of the session worth checking, such as failure modes automated rules
        found (``thearc.sessions.analysis.aeg.focus``); it gets them, as hints to confirm, under "Where to look".
        ``judged_from`` is the first event the reflection judges, when it judges only the rest of a session (its tail,
        after earlier reflections): its segment (``evidence["from_event"]`` to ``["to_event"]``) and digest start
        there; the reflector still reads the whole history.
        """
        schema = ReflectionSchema.of(schema)
        if not self.runtime.can_fork:
            raise ReflectionError("capability", f"{self.harness} cannot continue a session; use reflect()")
        installed = history.install(self.harness, home=self.home, overwrite=True, strict=False)
        try:
            counts = self.redaction.file(installed.path)
            if counts:
                installed = replace(installed, transformations=(
                    *installed.transformations, f"redacted before the reflector read it: {summary(counts)}"))
            return self._handoff(prompt, history, installed, agent, schema, provenance, extra, counts, focus,
                                 judged_from)
        finally:  # The copy only starts the reflector: left in the store, it would count as a session of the project.
            Path(installed.path).unlink(missing_ok=True)

    def _handoff(
        self,
        prompt: str | Prompt,
        history: EventSequence,
        installed: InstalledSession,
        agent: AgentConfig,
        schema: ReflectionSchema,
        provenance: str | None,
        extra: dict[str, Any],
        counts: Counter[str],
        focus: str | None = None,
        judged_from: str | None = None,
    ) -> Reflection:
        session = history if isinstance(history, Session) else getattr(history, "session", None)
        ids = [event["id"] for event in records(history.selected_events)]
        judged = ids[ids.index(judged_from):] if judged_from in ids else ids
        evidence = {
            "handoff": installed.session_id,
            # A thread (e.g. a session's root thread, without its subagents) records which one it was.
            **({"thread": history.id} if isinstance(history, Thread) else {}),
            "events": len(history.selected_events),
            "asked": fingerprint(prompt, schema),
            "tokens": estimate_tokens(Path(installed.path).read_text(encoding="utf-8")),
            **_redacted(counts),
            "digest": ids_digest(judged),
            **({"from_event": judged[0], "to_event": judged[-1]} if judged else {}),
            **({"session": session.id, "native_session": session.metadata.native_id} if session else {}),
            **({"provenance": provenance} if provenance else {}),
            **({"focus": focus} if focus else {}),
        }
        known = [UNRECORDED[session.metadata.harness]] if session and session.metadata.harness in UNRECORDED else []
        known += [provenance] if provenance else []
        task = prompt_text(prompt)
        if known:
            task += "\n\n## How the session ran (not in its record)\n\n" + "\n\n".join(known)
        if focus:
            task += "\n\n## Where to look\n\n" + focus
        return self._run(
            task, agent, schema, fork=installed, evidence=evidence, extra=extra, notes=installed.transformations,
            prompt=prompt,
        )

    def _run(
        self,
        task: str,
        agent: AgentConfig,
        schema: ReflectionSchema,
        *,
        fork: Any,
        evidence: dict[str, Any],
        extra: dict[str, Any],
        notes: tuple[str, ...] = (),
        mcp_tools: Sequence[ToolSpec] = (),
        prompt: str | Prompt | None = None,
    ) -> Reflection:
        files = agent.files()
        submission = Submission(files, schema, agent.is_frozen)
        if agent.frozen:
            task += ("\n\n## Frozen\n\nThese parts of the configuration are frozen — kept as they are, whatever "
                     "the evidence — so do not rate them: " + ", ".join(f"`{p}`" for p in agent.frozen) + ".")
        with tempfile.TemporaryDirectory(prefix="thearc-reflection-") as directory:
            workspace = files.write(Path(directory) / "agent")
            with submission.serve(*mcp_tools) as tool, self.runtime.conversation(
                workspace, tool, fork=fork
            ) as conversation:
                conversation.send(
                    f"{INSTRUCTIONS}\n\n## Task\n\n{task}\n\n## Answer\n\n" + SUBMIT.format(workspace=workspace)
                )
        if submission.items is None:
            raise ReflectionError(
                "no_submission", f"The reflector ended without an accepted {TOOL} call ({submission.attempts} rejected)"
            )
        reflector = {"harness": self.harness, "model": self.runtime.model, "session": conversation.id,
                     **({"usage": conversation.usage.to_dict()} if conversation.usage else {})}
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
            mode="handoff" if fork is not None else "attached",
            prompt=prompt_identity(prompt) if prompt is not None else None,
            schema=schema_hash(schema),
        )
