"""The one mapping from events to blocks: what a target format can hold, decided once for every writer.

Strict conversions refuse anything the target cannot represent faithfully; best-effort
ones keep it as labelled text (or the closest native form) and note it by event ID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..events import UnsupportedConversion, codex_control, content_record, has_media, native_codex_item

THINKING = {"reasoning", "thought", "thinking"}
TARGET_NAMES = {
    "codex": "Codex rollouts",
    "claude": "Claude transcripts",
    "pi": "Pi sessions",
    "opencode": "OpenCode sessions",
    "antigravity": "Antigravity transcripts",
}
_MESSAGE_ROLES = {"codex": {"user", "assistant", "system", "developer"}, "antigravity": {"user", "assistant", "system"}}


@dataclass
class Block:
    """One event, shaped for a writer.

    ``kind`` is ``text``, ``thinking``, ``tool_call``, ``tool_result``, ``compaction``,
    ``native`` (``native`` is taken as is), or ``control`` (a Codex runtime record).
    """

    event: dict
    role: str
    kind: str
    text: str = ""
    call_id: str | None = None
    name: str | None = None
    input: object = None
    status: str | None = None
    native: dict | None = None

    @property
    def is_error(self) -> bool:
        return self.status in {"failure", "error"}


class Conversion:
    """Refusals (strict) and notes (best-effort) for one export."""

    def __init__(self, strict: bool):
        self.strict = strict
        self.notes: list[str] = []

    def refuse(self, event: dict, reason: str) -> None:
        if self.strict:
            raise UnsupportedConversion(f"{reason}: {event['id']}")

    def note(self, event: dict, reason: str, outcome: str = "kept as labelled text") -> None:
        self.notes.append(f"{event['id']}: {reason}; {outcome}")

    def labelled(self, event: dict, reason: str, text: str, role: str = "assistant") -> Block:
        self.refuse(event, reason)
        self.note(event, reason)
        return Block(event, role, "text", text)


def to_blocks(events: list[dict], conversion: Conversion, target: str) -> list[Block]:
    blocks, calls = [], set()
    for event in events:
        block = _block(event, conversion, target)
        if block.kind == "tool_call":
            if block.call_id in calls:
                block = conversion.labelled(
                    event,
                    "Duplicate tool call ID needs an explicit identity mapping",
                    f"[Tool call {block.name}]\n{event['text']}",
                )
            else:
                calls.add(block.call_id)
        elif (
            block.kind == "tool_result"
            and block.call_id not in calls
            and not (target == "antigravity" and block.call_id is None)
        ):
            block = conversion.labelled(
                event,
                "Tool result has no preceding call in the selected events",
                f"[Tool result without its call]\n{event['text']}",
                role="user",
            )
        blocks.append(block)
    return blocks


def _block(event: dict, conversion: Conversion, target: str) -> Block:
    kind = event["kind"]
    if kind == "compaction":
        return Block(event, "system", "compaction", event["text"])
    if target == "codex" and codex_control(event):
        return Block(event, "system", "control", native=event["raw"])
    if native_codex_item(event):
        return _codex_item(event, conversion, target)
    if kind in THINKING:
        return _thinking(event, conversion, target)
    if kind == "message" and event["role"] in {"user", "assistant", "system", "developer"}:
        return _message(event, conversion, target)
    if kind == "tool_call" and event["call_id"] and event["tool_name"]:
        return _tool_call(event, conversion, target)
    if kind == "tool_result" and (event["call_id"] or target == "antigravity"):
        return Block(
            event,
            "tool",
            "tool_result",
            event["text"],
            call_id=event["call_id"] or None,
            name=event["tool_name"],
            status=event["status"],
        )
    return conversion.labelled(
        event,
        f"No conversion for {kind!r} events; use canonical JSONL for a faithful copy",
        f"[{kind} record]\n{event['text']}".rstrip(),
    )


def _message(event: dict, conversion: Conversion, target: str) -> Block:
    role, text = event["role"], event["text"]
    if has_media(content_record(event)):
        conversion.refuse(event, "Multimodal conversion is unsupported")
        conversion.note(event, "media block omitted", "text kept")
        text = (text + "\n" if text else "") + "[Media omitted]"
    if role not in _MESSAGE_ROLES.get(target, {"user", "assistant"}):
        conversion.refuse(event, f"{TARGET_NAMES[target]} have no {role} messages")
        conversion.note(event, f"{role} message")
        role, text = "user", f"[{role.capitalize()} message]\n{text}"
    return Block(event, role, "text", text)


def _thinking(event: dict, conversion: Conversion, target: str) -> Block:
    if target not in {"claude", "codex"}:
        return Block(event, "assistant", "thinking", event["text"])
    if target == "claude":
        native = _signed_claude_thinking(event)
        if native is not None:
            return Block(event, "assistant", "native", native=native)
        reason = f"Thinking from {event['harness']} has no Claude signature"
        conversion.refuse(event, reason)
        conversion.note(event, reason[0].lower() + reason[1:])
    # Codex: foreign thinking is readable evidence, not a native (encrypted) reasoning item.
    return Block(
        event,
        "assistant",
        "text",
        "[Historical thinking record]\n" + (event["text"] or "[No readable thinking text was recorded.]"),
    )


def _signed_claude_thinking(event: dict) -> dict | None:
    """The original Claude thinking block, if it still carries the signature Claude requires on replay."""
    block = content_record(event) if event["harness"] == "claude" else None
    if isinstance(block, dict) and (
        block.get("type") == "thinking"
        and block.get("signature")
        or block.get("type") == "redacted_thinking"
        and block.get("data")
    ):
        return block
    return None


def _tool_call(event: dict, conversion: Conversion, target: str) -> Block:
    args = event["arguments"]
    if isinstance(args, str) and target != "codex":
        try:
            args = json.loads(args)
        except ValueError:
            conversion.refuse(event, f"{TARGET_NAMES[target]} need JSON tool arguments")
            conversion.note(event, "tool arguments are not JSON", 'wrapped as {"raw_arguments": ...}')
            args = {"raw_arguments": args}
    return Block(event, "assistant", "tool_call", call_id=event["call_id"], name=event["tool_name"], input=args)


def _codex_item(event: dict, conversion: Conversion, target: str) -> Block:
    """Codex encrypted reasoning and custom tool calls: kept as is for Codex, converted for the rest."""
    payload = event["raw"]["payload"]
    kind, name = payload["type"], TARGET_NAMES[target]
    # Encrypted reasoning is bound to its item ID, so it keeps it; other items drop theirs.
    dropped = {"internal_chat_message_metadata_passthrough"} | (set() if kind == "reasoning" else {"id"})
    item = {k: v for k, v in payload.items() if k not in dropped}
    if kind == "custom_tool_call":
        if target != "codex":
            conversion.refuse(event, f"Codex custom tool call {item.get('name')!r} has no {name} equivalent")
            conversion.note(event, "Codex custom tool call", 'input wrapped as {"input": ...}')
        return Block(
            event,
            "assistant",
            "tool_call",
            call_id=item.get("call_id"),
            name=item.get("name"),
            input={"input": item.get("input")},
            native=item if target == "codex" else None,
        )
    if kind == "custom_tool_call_output":
        return Block(
            event,
            "tool",
            "tool_result",
            str(item.get("output", "")),
            call_id=item.get("call_id"),
            name=event["tool_name"],
            status=event["status"],
            native=item if target == "codex" else None,
        )
    if target == "codex":
        return Block(event, "assistant", "native", native=item)
    conversion.refuse(event, f"Codex encrypted reasoning cannot be exported to {name}")
    summary = "\n".join(
        part["text"]
        for part in item.get("summary") or []
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )
    conversion.note(
        event, "Codex encrypted reasoning", "summary kept as labelled text" if summary else "replaced by a placeholder"
    )
    text = f"[Codex reasoning summary]\n{summary}" if summary else "[Encrypted Codex reasoning omitted]"
    return Block(event, "assistant", "text" if target == "claude" else "thinking", text)
