"""Split a session's event streams into trajectories at user prompts, without filtering contents."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING

from .models import Event

if TYPE_CHECKING:
    from .session import TrajectorySplit


@dataclass
class _Draft:
    """A trajectory while it is being split; frozen into a ``Trajectory`` at the end."""

    prompt_events: list[Event]
    events: list[Event] = field(default_factory=list)
    boundary: str = "end_of_stream"
    native_turn_id: str | None = None


def _stream(event: Event) -> tuple:
    return (event.source_id, event.session_id, event.run_id, event.reference.path, event.reference.generation)


def _record(event: Event) -> tuple:
    if event.harness == "opencode":
        # One export line contains many messages; only parts of the same message
        # belong to one prompt.
        message_pointer = "/".join(event.raw_pointer.split("/")[:3])
        return (*_stream(event), event.reference.byte_offset, message_pointer)
    return (*_stream(event), event.reference.byte_offset, event.reference.line)


def _user_prompt(event: Event) -> bool:
    return event.kind == "message" and event.role == "user"


def _native_marker(event: Event) -> tuple[str | None, str | None]:
    """Use recorded Codex lifecycle IDs, never timestamps or inferred ordinals."""
    if event.harness != "codex":
        return None, None
    payload = event.raw.get("payload")
    if not isinstance(payload, dict):
        return None, None
    record_type = event.raw.get("type")
    kind = payload.get("type") if record_type == "event_msg" else record_type
    if kind not in {"task_started", "turn_context", "task_complete", "task_completed", "turn_aborted"}:
        return None, None
    turn_id = payload.get("turn_id")
    return kind, turn_id if isinstance(turn_id, str) and turn_id else None


def _context_records(events: list[Event]) -> set[tuple]:
    """Identify native setup preceding a confirmed Codex user message.

    Codex emits environment setup with role=user before turn_context. A later
    UserMessage lifecycle record confirms which response_item was the actual
    request. Do not classify by matching special strings in the user's text.
    """
    context: set[tuple] = set()
    before_context: set[tuple] = set()
    users: dict[tuple, list[Event]] = {}
    in_context = False
    for event in events:
        marker, _ = _native_marker(event)
        if marker == "task_started":
            before_context = set()
            users = {}
            in_context = True
        elif marker == "turn_context":
            in_context = False
        if _user_prompt(event):
            users.setdefault(_record(event), []).append(event)
            if in_context:
                before_context.add(_record(event))
        payload = event.raw.get("payload", {})
        if event.harness != "codex" or not isinstance(payload, dict):
            continue
        item = payload.get("item", {})
        text = None
        if (payload.get("type") == "item_completed" and isinstance(item, dict)
                and item.get("type") == "UserMessage"):
            from .adapters import text_content

            text = text_content(item.get("content", []))
        elif payload.get("type") == "user_message":
            text = payload.get("message")
        if isinstance(text, str):
            for key, blocks in reversed(list(users.items())):
                if "\n".join(block.text for block in blocks) == text:
                    context.update(before_context - {key})
                    break
    return context


def split_trajectories(events: Sequence[Event]) -> TrajectorySplit:
    """One trajectory per normalized user message, preserving supplied order.

    Adjacent content blocks of the same native user record form one prompt.
    Tool results and assistant messages never start trajectories. Explicit native
    completion/interruption markers close a trajectory; otherwise the next prompt
    or stream end bounds it without claiming completion. Unattached metadata and
    activity preceding a prompt remain visible in unassigned_events.
    """
    from .session import Trajectory, TrajectorySplit

    drafts: list[_Draft] = []
    unassigned: list[Event] = []
    for _, segment in groupby(events, key=_stream):
        segment = list(segment)
        context_records = _context_records(segment)
        current: _Draft | None = None
        pending_turn: str | None = None
        for event in segment:
            marker, turn_id = _native_marker(event)
            if _user_prompt(event) and _record(event) not in context_records:
                if (current is not None and not current.events
                        and _record(current.prompt_events[-1]) == _record(event)):
                    current.prompt_events.append(event)
                    continue
                if current is not None:
                    pending_turn = pending_turn or current.native_turn_id
                    current.boundary = "next_prompt"
                current = _Draft([event], native_turn_id=pending_turn)
                drafts.append(current)
                pending_turn = None
                continue
            if marker in {"task_started", "turn_context"} and turn_id:
                if current is None or current.native_turn_id not in {None, turn_id}:
                    pending_turn = turn_id
                elif current.native_turn_id is None:
                    current.native_turn_id = turn_id
            if current is None:
                unassigned.append(event)
                continue
            current.events.append(event)
            if marker in {"task_complete", "task_completed", "turn_aborted"}:
                if turn_id and current.native_turn_id not in {None, turn_id}:
                    continue  # A different native turn cannot close this trajectory.
                current.native_turn_id = turn_id or current.native_turn_id
                current.boundary = "interrupted" if marker == "turn_aborted" else "completed"
                current = None
                pending_turn = None
        if current is not None:
            current.boundary = "stream_boundary"
    # Only the final open trajectory reaches the end of the supplied stream.
    if drafts and events:
        last = drafts[-1]
        if last.boundary == "stream_boundary" and _stream(last.prompt_events[0]) == _stream(events[-1]):
            last.boundary = "end_of_stream"
    # Several user prompts may be injected within one native turn. Such a turn
    # cannot isolate any individual trajectory, so don't advertise it for handoff.
    counts = Counter((d.prompt_events[0].session_id, d.native_turn_id) for d in drafts if d.native_turn_id)
    for draft in drafts:
        if counts[(draft.prompt_events[0].session_id, draft.native_turn_id)] > 1:
            draft.native_turn_id = None
    return TrajectorySplit(tuple(Trajectory(tuple(d.prompt_events), tuple(d.events), d.boundary, d.native_turn_id)
                                 for d in drafts), tuple(unassigned))
