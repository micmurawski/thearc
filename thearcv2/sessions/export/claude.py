"""Claude Code transcripts: one record per event, parent-linked, with the fields Claude Code expects."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .blocks import Block, Conversion
from .identity import Identity


def write(blocks: list[Block], conversion: Conversion, identity: Identity) -> tuple[list[dict[str, Any]], list[str]]:
    output, previous = [], None
    for block in blocks:
        event = block.event
        common = {"isSidechain": False, "userType": "external", "entrypoint": "cli", "cwd": identity.cwd,
                  "sessionId": identity.session_id, "version": "thearc", "timestamp": event["timestamp"]}
        if block.kind == "compaction":
            boundary = str(uuid5(NAMESPACE_URL, event["id"] + ":boundary"))
            output.append({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted",
                           "isMeta": False, "uuid": boundary, "parentUuid": None, "logicalParentUuid": previous,
                           "level": "info", "compactMetadata": {"trigger": "manual", "preTokens": 0}, **common})
            previous = str(uuid5(NAMESPACE_URL, event["id"]))
            output.append({"type": "user", "uuid": previous, "parentUuid": boundary, "isCompactSummary": True,
                           "message": {"role": "user", "content": block.text}, **common})
            continue
        role = "assistant" if block.role == "assistant" else "user"
        if block.kind == "native":
            content = [block.native]
        elif block.kind == "tool_call":
            content = [{"type": "tool_use", "id": block.call_id, "name": block.name, "input": block.input}]
        elif block.kind == "tool_result":
            content = [{"type": "tool_result", "tool_use_id": block.call_id, "content": block.text,
                        "is_error": block.is_error}]
        else:
            content = [{"type": "text", "text": block.text}]
        message: dict[str, Any] = {"role": role, "content": content}
        if role == "assistant":
            message |= {"model": "imported", "usage": {"input_tokens": 0, "output_tokens": 0},
                        "stop_reason": "tool_use" if block.kind == "tool_call" else "end_turn"}
        identifier = str(uuid5(NAMESPACE_URL, event["id"]))
        output.append({"type": role, "uuid": identifier, "parentUuid": previous, **common, "message": message})
        previous = identifier
    return output, [f"Events mapped to Claude content blocks; message IDs regenerated; session {identity.session_id}."]
