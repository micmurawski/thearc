"""Pi sessions: a header, then a parent-linked chain of user, assistant, toolResult, and compaction entries."""

from __future__ import annotations

import json
from typing import Any

from .blocks import Block, Conversion
from .identity import Clock, Identity, iso


def _tokens(text: str) -> int:
    return -(-len(text) // 4)  # A rough estimate: Pi needs usage numbers (it crashes without), not exact ones.


def _usage(input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "input": input_tokens,
        "output": output_tokens,
        "cacheRead": 0,
        "cacheWrite": 0,
        "reasoning": 0,
        "totalTokens": input_tokens + output_tokens,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
    }


def write(blocks: list[Block], conversion: Conversion, identity: Identity) -> tuple[list[dict[str, Any]], list[str]]:
    clock = Clock()
    start = clock(blocks[0].event) if blocks else 0
    records = [
        {"type": "session", "version": 3, "id": identity.session_id, "timestamp": iso(start), "cwd": identity.cwd}
    ]
    parent, assistant, pending_input = None, None, 0

    def append(entry: dict[str, Any]) -> dict[str, Any]:
        nonlocal parent
        records.append({"id": f"{len(records):08x}", "parentId": parent, **entry})
        parent = records[-1]["id"]
        return records[-1]

    for block in blocks:
        ms = clock(block.event)
        if block.role == "assistant":
            if assistant is None:
                assistant = append(
                    {
                        "type": "message",
                        "timestamp": iso(ms),
                        "message": {
                            "role": "assistant",
                            "content": [],
                            "api": "openai-completions",
                            "provider": "thearc",
                            "model": "imported",
                            "stopReason": "stop",
                            "usage": _usage(pending_input, 0),
                            "timestamp": ms,
                        },
                    }
                )
                pending_input = 0
            message = assistant["message"]
            if block.kind == "tool_call":
                message["content"].append(
                    {"type": "toolCall", "id": block.call_id, "name": block.name, "arguments": block.input}
                )
                message["stopReason"] = "toolUse"
            elif block.kind == "thinking":
                message["content"].append({"type": "thinking", "thinking": block.text})
            else:
                message["content"].append({"type": "text", "text": block.text})
            output = message["usage"]["output"] + _tokens(block.text or json.dumps(block.input or ""))
            message["usage"] = _usage(message["usage"]["input"], output)
            continue
        assistant = None
        if block.kind == "compaction":
            append(
                {
                    "type": "compaction",
                    "timestamp": iso(ms),
                    "summary": block.text,
                    "firstKeptEntryId": f"{len(records) + 1:08x}",
                    "tokensBefore": 0,
                    "details": {},
                    "fromHook": True,
                }
            )
            continue
        pending_input += _tokens(block.text)
        if block.kind == "tool_result":
            message = {
                "role": "toolResult",
                "toolCallId": block.call_id,
                "toolName": block.name or "",
                "content": [{"type": "text", "text": block.text}],
                "isError": block.is_error,
            }
        else:
            message = {"role": "user", "content": [{"type": "text", "text": block.text}]}
        append({"type": "message", "timestamp": iso(ms), "message": {**message, "timestamp": ms}})
    return records, [
        "Events mapped to Pi session entries; entry IDs regenerated.",
        "Token usage estimated from text length.",
    ]
