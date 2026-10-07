"""Antigravity ``transcript.jsonl`` steps. Calls carry no IDs there, so results must follow in call order."""

from __future__ import annotations

from typing import Any

from .blocks import Block, Conversion
from .identity import Clock, Identity, iso


def write(blocks: list[Block], conversion: Conversion, identity: Identity) -> tuple[list[dict[str, Any]], list[str]]:
    clock = Clock()
    steps: list[dict[str, Any]] = []
    waiting: list[str | None] = []
    planner: dict[str, Any] | None = None
    planner_record: tuple | None = None

    def step(kind: str, source: str, ms: int, **fields: Any) -> dict[str, Any]:
        record = {"step_index": len(steps), "source": source, "type": kind, "status": "DONE",
                  "created_at": iso(ms), **fields}
        if identity.cwd and not steps:
            record["cwd"] = identity.cwd
        steps.append(record)
        return record

    def result(block: Block, ms: int) -> None:
        status = {"status": "ERROR"} if block.is_error else {}
        if not status and block.event["harness"] == "antigravity" and block.event["raw"].get("status") == "RUNNING":
            status = {"status": "RUNNING"}
        step("GENERIC", "MODEL", ms, content=block.text, **status)

    for block in blocks:
        ms = clock(block.event)
        if block.role == "assistant":
            # A planner step ends with its tool calls; Antigravity's own steps also keep their boundaries.
            record = (block.event["reference"]["path"], block.event["reference"]["byte_offset"])
            if planner is not None and (planner.get("tool_calls") and block.kind != "tool_call" or
                                        block.event["harness"] == "antigravity" and record != planner_record):
                planner = None
            if planner is None:
                planner, planner_record, waiting = step("PLANNER_RESPONSE", "MODEL", ms), record, []
            if block.kind == "tool_call":
                planner.setdefault("tool_calls", []).append({"name": block.name, "args": block.input})
                waiting.append(block.call_id)
            elif block.text:
                field = "thinking" if block.kind == "thinking" else "content"
                planner[field] = f"{planner[field]}\n\n{block.text}" if planner.get(field) else block.text
            continue
        planner = None
        if block.kind == "compaction":
            waiting = []
            step("CHECKPOINT", "SYSTEM", ms, content=block.text)
        elif block.kind == "tool_result":
            if waiting and waiting[0] == block.call_id:
                waiting.pop(0)
            elif waiting or block.call_id is not None:
                conversion.refuse(block.event, "Antigravity pairs tool results with calls by order; "
                                               "this result does not follow its call")
                conversion.note(block.event, "tool result out of call order", "kept; it may pair with another call")
            result(block, ms)
        elif block.role == "system":
            step("SYSTEM_MESSAGE", "SYSTEM", ms, content=block.text)
        else:
            waiting = []
            step("USER_INPUT", "USER_EXPLICIT", ms, content=block.text)
    return steps, ["Events mapped to Antigravity steps; tool results written as GENERIC steps in call order."]
