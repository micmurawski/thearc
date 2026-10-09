"""Helpers for normalized events: validation, immutable copies, and what a record holds."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .immutable import Record
from .models import Event

EventDict = dict[str, Any]
"""A normalized event as JSON data."""
EventLike = Iterable[Event | Record | EventDict] | str
"""Events as models, immutable records, dicts, or a JSON string of them."""


class UnsupportedConversion(ValueError):
    """The events cannot be represented faithfully in the requested form."""


_CODEX_CONTROL_RECORDS = {"session_meta", "turn_context", "world_state", "token_usage_record"}
_CODEX_CONTROL_MESSAGES = {
    "task_started",
    "task_complete",
    "task_completed",
    "turn_aborted",
    "token_count",
    "item_started",
    "item_completed",
    "user_message",
    "agent_message",
    "agent_reasoning",
    "thread_settings_applied",
}


def records(value: EventLike) -> list[EventDict]:
    """Events (models, records, dicts, or a JSON string of them) as validated JSON dicts."""
    if isinstance(value, str):
        value = json.loads(value)
    result = []
    for entry in value:
        if hasattr(entry, "model_dump"):
            entry = entry.model_dump(mode="json")
        event = Event.model_validate(entry)
        if any(not getattr(event, key).strip() for key in ("id", "source_id", "session_id", "run_id")):
            raise ValueError("Canonical events require nonempty identities")
        result.append(event.model_dump(mode="json"))
    return result


def immutable_events(events: EventLike) -> tuple[Record, ...]:
    return tuple(Record(e) for e in records(events))


def content_record(event: EventDict) -> Any:
    """The part of the raw native record this event came from (its ``json_pointer``), else the whole record."""
    value = event["raw"]
    pointer = event["reference"].get("json_pointer", "")
    if pointer.startswith("/"):
        try:
            for token in pointer[1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                value = value[int(token)] if isinstance(value, list) else value[token]
            return value
        except (KeyError, ValueError, TypeError, IndexError):
            pass  # Some references point into an outer record; raw is already the content.
    return event["raw"]


def has_media(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("type") in {"image", "image_url", "input_image", "audio", "input_audio"} or any(
            has_media(child) for child in value.values()
        )
    return isinstance(value, list) and any(has_media(child) for child in value)


def native_codex_item(event: EventDict) -> bool:
    """A Codex response item kept as is: encrypted reasoning or a custom tool call and its output."""
    raw, payload = event["raw"], event["raw"].get("payload")
    return (
        event["harness"] == "codex"
        and raw.get("type") == "response_item"
        and isinstance(payload, dict)
        and payload.get("type") in {"reasoning", "custom_tool_call", "custom_tool_call_output"}
    )


def provenance_only(event: EventDict) -> bool:
    """Metadata kept in the index but never replayed as conversation (lifecycle records, attachments, headers)."""
    return event["kind"] == "metadata" and not native_codex_item(event)


def codex_control(event: EventDict) -> bool:
    """Codex runtime records (headers, turn lifecycle) that a Codex export carries over unchanged."""
    raw = event["raw"]
    if event["harness"] != "codex" or event["kind"] != "metadata":
        return False
    payload = raw.get("payload")
    return raw.get("type") in _CODEX_CONTROL_RECORDS or (
        raw.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") in _CODEX_CONTROL_MESSAGES
    )


def reasoning_texts(event: EventDict) -> list[str]:
    """Readable summary and content of a Codex reasoning record; the encrypted part has none."""
    payload = event["raw"].get("payload", {})
    if not isinstance(payload, dict) or payload.get("type") != "reasoning":
        return []
    return [
        block["text"]
        for block in [*(payload.get("summary") or []), *(payload.get("content") or [])]
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
