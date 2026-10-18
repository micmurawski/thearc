"""Codex rollouts: a ``session_meta`` header, Responses items, and one Codex turn per trajectory."""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ..models import Event
from .blocks import Block, Conversion
from .identity import Identity, epoch_ms

NOTES = [
    "Events mapped to Responses items; source identities retained by export event_ids.",
    "Tool results with a status carry it as a Codex exit-code block (0 success, 1 failure).",
    "Foreign thinking records become labeled assistant text, not native reasoning items.",
]


def write(blocks: list[Block], conversion: Conversion, identity: Identity) -> tuple[list[dict[str, Any]], list[str]]:
    events = [block.event for block in blocks]
    output = [_record(block) for block in blocks]
    notes = []
    if not any(_starts_turn(record) for record in output):
        output, turns = _with_turns(events, output)
        notes.append(f"{turns} Codex turn(s) marked, one per trajectory, so Codex can list and fork them.")
    headers = [i for i, record in enumerate(output) if record.get("type") == "session_meta"]
    paginated = False
    for i in headers:  # A copy under a new ID must not claim the original's identity.
        payload = dict(output[i].get("payload", {}))
        # "paginated" history lives in Codex's own store, not in the rollout; a copy is only the rollout,
        # so with the flag kept Codex would find no turns in it.
        paginated = payload.pop("history_mode", None) == "paginated" or paginated
        if payload.get("id") != identity.session_id:
            payload["id"] = identity.session_id
            if "session_id" in payload:
                payload["session_id"] = identity.session_id
        output[i] = {**output[i], "payload": payload}
    if paginated:
        notes.append("Codex history_mode dropped: the copy is a self-contained rollout, read from the file.")
    if not headers:
        output.insert(
            0,
            {
                "timestamp": identity.started,
                "type": "session_meta",
                "payload": {
                    "id": identity.session_id,
                    "timestamp": identity.started,
                    "cwd": identity.cwd,
                    "originator": "thearc",
                    "cli_version": "thearc",
                    "source": "cli",
                    "model_provider": "thearc",
                },
            },
        )
        notes.append("Codex session_meta header synthesized for a session recorded elsewhere.")
    return output, notes + NOTES


def _record(block: Block) -> dict[str, Any]:
    timestamp = block.event["timestamp"]
    if block.kind == "control" and block.native is not None:
        return block.native
    if block.kind == "compaction":
        return {"type": "compacted", "timestamp": timestamp, "payload": {"message": block.text}}
    return {"type": "response_item", "timestamp": timestamp, "payload": _item(block)}


def _item(block: Block) -> dict[str, Any]:
    if block.native is not None:
        return block.native
    if block.kind == "tool_call":
        arguments = block.input if isinstance(block.input, str) else json.dumps(block.input, ensure_ascii=False)
        return {"type": "function_call", "call_id": block.call_id, "name": block.name, "arguments": arguments}
    if block.kind == "tool_result":
        output: str | list[dict[str, str]] = block.text
        if block.status in {"success", "failure", "error"}:
            # Codex's own form: text blocks, the last one reporting the exit code.
            output = [
                {"type": "input_text", "text": block.text},
                {"type": "input_text", "text": json.dumps({"exit_code": 1 if block.is_error else 0})},
            ]
        return {"type": "function_call_output", "call_id": block.call_id, "output": output}
    content_type = "output_text" if block.role == "assistant" else "input_text"
    return {"type": "message", "role": block.role, "content": [{"type": content_type, "text": block.text}]}


def _starts_turn(record: dict[str, Any]) -> bool:
    payload = record.get("payload")
    return record.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "task_started"


def _with_turns(events: list[dict[str, Any]], output: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Wrap each trajectory in Codex turn lifecycle records; events outside trajectories stay outside turns."""
    from ..trajectories import split_trajectories

    position = {event["id"]: index for index, event in enumerate(events)}
    before, after = {}, {}
    split = split_trajectories([Event.model_validate(event) for event in events])
    for trajectory in split.trajectories:
        turn = str(uuid5(NAMESPACE_URL, f"thearc-turn:{trajectory.id}"))
        members = [*trajectory.prompt_events, *trajectory.events]
        times = [ms for e in members if e.timestamp and (ms := epoch_ms(e.timestamp)) is not None] or [0]
        started, completed = times[0] // 1000, times[-1] // 1000
        before[position[members[0].id]] = {
            "timestamp": members[0].timestamp,
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": turn, "started_at": started},
        }
        if trajectory.boundary == "interrupted":
            payload = {"type": "turn_aborted", "turn_id": turn, "reason": "interrupted"}
        else:
            reply = next(
                (e.text for e in reversed(trajectory.events) if e.kind == "message" and e.role == "assistant"), ""
            )
            payload = {"type": "task_complete", "turn_id": turn, "last_agent_message": reply}
        after[position[members[-1].id]] = {
            "timestamp": members[-1].timestamp,
            "type": "event_msg",
            "payload": {
                **payload,
                "started_at": started,
                "completed_at": completed,
                "duration_ms": max(0, times[-1] - times[0]),
            },
        }
    marked = []
    for index, record in enumerate(output):
        marked += [before[index]] if index in before else []
        marked.append(record)
        marked += [after[index]] if index in after else []
    return marked, len(split.trajectories)
