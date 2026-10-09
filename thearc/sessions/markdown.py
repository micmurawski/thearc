"""Events rendered as Markdown evidence: every event, with its ID, in the order given."""

from __future__ import annotations

import json
import re

from .events import (
    EventDict,
    EventLike,
    UnsupportedConversion,
    content_record,
    has_media,
    provenance_only,
    reasoning_texts,
    records,
)


def fenced(text: str, language: str = "text") -> str:
    fence = "`" * max(3, 1 + max((len(s) for s in re.findall(r"`+", text)), default=0))
    return f"{fence}{language}\n{text}\n{fence}"


def _mirrors_arguments(event: EventDict) -> bool:
    """Adapters store tool arguments as text too; render them once."""
    try:
        return json.loads(event["text"]) == event["arguments"]
    except ValueError:
        return event["text"] == event["arguments"]


def evidence_to_markdown(events_or_json: EventLike) -> str:
    """Render events in the order given; nothing is selected or truncated."""
    events = records(events_or_json)
    calls = {(e["run_id"], e["call_id"]): e["id"] for e in events if e["kind"] == "tool_call" and e["call_id"]}
    parts, omitted = [], 0
    for event in events:
        reasoning = reasoning_texts(event) if event["kind"] == "metadata" else []
        if provenance_only(event) and not event["text"] and not reasoning:
            omitted += 1  # Bookkeeping with nothing readable is counted, not printed as a bare heading.
            continue
        details = [event[k] for k in ("role", "tool_name", "status") if event[k]]
        # JSON quoting keeps embedded line breaks from changing the heading structure.
        parts.append(
            f"### Event {json.dumps(event['id'], ensure_ascii=False)} — {event['kind'].replace('_', ' ')}"
            + (": " + ", ".join(details) if details else "")
        )
        if event["kind"] == "tool_result" and event["call_id"]:
            call = calls.get((event["run_id"], event["call_id"]))
            parts.append(
                "Responds to: "
                + json.dumps(call or event["call_id"])
                + (" (call outside selection)" if call is None else "")
            )
        if event["arguments"] is not None:
            parts.append(fenced(json.dumps(event["arguments"], ensure_ascii=False, indent=2), "json"))
        if event["text"] and not _mirrors_arguments(event):
            parts.append(fenced(event["text"]))
        payload = event["raw"].get("payload")
        if event["kind"] == "metadata" and isinstance(payload, dict) and payload.get("type") == "reasoning":
            parts.extend(fenced(text) for text in reasoning)
            if payload.get("encrypted_content"):
                parts.append("Encrypted reasoning is retained in the canonical record; no readable text is inferred.")
        content = content_record(event)
        if event["kind"] == "unknown" or has_media(content):
            if content is event["raw"]:
                raise UnsupportedConversion(f"No isolated content block for Markdown rendering: {event['id']}")
            parts.append(fenced(json.dumps(content, ensure_ascii=False, indent=2), "json"))
    if omitted:
        parts.append(
            f"_{omitted} metadata record(s) without readable content omitted; they remain in the indexed session._"
        )
    return "\n\n".join(parts) + ("\n" if parts else "")
