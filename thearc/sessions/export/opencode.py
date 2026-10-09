"""OpenCode: one export object (session info and messages); tool results merge into their tool parts."""

from __future__ import annotations

from typing import Any

from .blocks import Block, Conversion
from .identity import Clock, Identity, opencode_id

_AGENT = {"agent": "thearc", "model": {"providerID": "thearc", "modelID": "imported"}}
_TOKENS = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}


def write(blocks: list[Block], conversion: Conversion, identity: Identity) -> tuple[list[dict[str, Any]], list[str]]:
    clock = Clock()
    session_id, cwd = identity.session_id, identity.cwd
    created = clock(blocks[0].event) if blocks else 0
    messages, assistant, calls, parent = [], None, {}, ""

    def message(role: str, seed: str, ms: int, parts: list[dict[str, Any]], **info: Any) -> dict[str, Any]:
        message_id = opencode_id("msg", ms, seed)
        fields = (
            _AGENT
            if role == "user"
            else {
                "modelID": "imported",
                "providerID": "thearc",
                "mode": "build",
                "agent": "thearc",
                "path": {"cwd": cwd, "root": cwd},
                "cost": 0,
                "tokens": _TOKENS,
            }
        )
        time = {"created": ms} if role == "user" else {"created": ms, "completed": ms}
        entry = {
            "info": {"id": message_id, "sessionID": session_id, "role": role, "time": time, **fields, **info},
            "parts": [
                {"id": opencode_id("prt", ms, seed), "sessionID": session_id, "messageID": message_id, **part}
                for part in parts
            ],
        }
        messages.append(entry)
        return entry

    for block in blocks:
        ms = clock(block.event)
        seed = block.event["id"]
        if block.role == "assistant":
            if assistant is None:
                assistant = message("assistant", seed, ms, [], parentID=parent)
                assistant["info"]["parentID"] = parent or assistant["info"]["id"]
            assistant["info"]["time"]["completed"] = ms
            part = {"id": opencode_id("prt", ms, seed), "sessionID": session_id, "messageID": assistant["info"]["id"]}
            if block.kind == "tool_call":
                part.update(
                    type="tool",
                    callID=block.call_id,
                    tool=block.name,
                    state={"status": "pending", "input": block.input},
                )
                calls[block.call_id] = part
            else:
                part.update(
                    type="reasoning" if block.kind == "thinking" else "text",
                    text=block.text,
                    time={"start": ms, "end": ms},
                )
            assistant["parts"].append(part)
            continue
        if block.kind == "tool_result" and block.call_id in calls:
            call, timing = calls[block.call_id], {"start": ms, "end": ms}
            call["state"] = (
                {"status": "error", "input": call["state"]["input"], "error": block.text, "time": timing}
                if block.is_error
                else {
                    "status": "completed",
                    "input": call["state"]["input"],
                    "output": block.text,
                    "title": block.name or "",
                    "metadata": {},
                    "time": timing,
                }
            )
            continue
        assistant = None
        if block.kind == "compaction":
            marker = message("user", seed, ms, [{"type": "compaction", "auto": True}])
            parent = message(
                "assistant",
                seed + ":summary",
                ms,
                [{"type": "text", "text": block.text, "time": {"start": ms, "end": ms}}],
                parentID=marker["info"]["id"],
                summary=True,
            )["info"]["id"]
            continue
        parent = message("user", seed, ms, [{"type": "text", "text": block.text, "time": {"start": ms, "end": ms}}])[
            "info"
        ]["id"]
    title = next(
        (
            b.text.strip().splitlines()[0][:80]
            for b in blocks
            if b.role == "user" and b.kind == "text" and b.text.strip()
        ),
        "Imported session",
    )
    words = [w for w in "".join(c.lower() if c.isalnum() else "-" for c in title).split("-") if w]
    info = {
        "id": session_id,
        "slug": "-".join(words[:8]) or "imported-session",
        "projectID": "global",
        "directory": cwd,
        "title": title,
        "version": "thearc",
        "time": {"created": created, "updated": clock.ms},
    }
    return [{"info": info, "messages": messages}], [
        "Events mapped to one OpenCode export object; tool results merged into their tool parts.",
        "Session, message, and part IDs regenerated deterministically.",
    ]
