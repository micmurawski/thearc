"""Explicit evidence rendering and provider exports, without event selection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from thearc.learning.sessions.models import Event

from .immutable import Record


class UnsupportedConversion(ValueError):
    pass


def records(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, (list, tuple)):
        value = list(value)
    result = []
    for entry in value:
        if hasattr(entry, "model_dump"):
            entry = entry.model_dump(mode="json")
        event = Event.model_validate(entry)
        if any(not getattr(event, key).strip() for key in ("id", "source_id", "session_id", "run_id")):
            raise ValueError("Canonical events require nonempty identities")
        result.append(event.model_dump(mode="json"))
    return result


def fenced(text, language="text"):
    fence = "`" * max(3, 1 + max((len(s) for s in re.findall(r"`+", text)), default=0))
    return f"{fence}{language}\n{text}\n{fence}"


def _content_record(event):
    value = event["raw"]
    pointer = event["reference"].get("json_pointer", "")
    if pointer.startswith("/"):
        try:
            for token in pointer[1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                value = value[int(token)] if isinstance(value, list) else value[token]
            return value
        except (KeyError, ValueError, TypeError, IndexError):
            pass  # Some corpus references point into an outer record; raw is already extracted.
    return event["raw"]


def _has_media(value):
    if isinstance(value, dict):
        return (isinstance(value.get("type"), str) and
                value["type"] in {"image", "image_url", "input_image", "audio", "input_audio"}) or any(
            _has_media(child) for child in value.values()
        )
    return isinstance(value, list) and any(_has_media(child) for child in value)


def evidence_to_markdown(events_or_json) -> str:
    """Render canonical events/JSON in supplied order; never select or truncate."""
    events = records(events_or_json)
    calls = {(e["run_id"], e["call_id"]): e["id"] for e in events
             if e["kind"] == "tool_call" and e["call_id"]}
    parts = []
    for event in events:
        label = event["kind"].replace("_", " ")
        details = [event[k] for k in ("role", "tool_name", "status") if event[k]]
        # JSON quoting keeps embedded line breaks from changing heading structure.
        title = json.dumps(event["id"], ensure_ascii=False)
        parts.append(f"### Event {title} — {label}" + (": " + ", ".join(details) if details else ""))
        if event["kind"] == "tool_result" and event["call_id"]:
            ref = calls.get((event["run_id"], event["call_id"]))
            parts.append("Responds to: " + json.dumps(ref or event["call_id"]) +
                         (" (call outside selection)" if ref is None else ""))
        if event["arguments"] is not None:
            parts.append(fenced(json.dumps(event["arguments"], ensure_ascii=False, indent=2), "json"))
        if event["text"]:
            parts.append(fenced(event["text"]))
        payload = event["raw"].get("payload", {})
        if event["kind"] == "metadata" and isinstance(payload, dict) and payload.get("type") == "reasoning":
            for block in [*(payload.get("summary") or []), *(payload.get("content") or [])]:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(fenced(block["text"]))
            if payload.get("encrypted_content"):
                parts.append("Encrypted reasoning is retained in the canonical record; no readable text is inferred.")
        content = _content_record(event)
        if event["kind"] == "unknown" or _has_media(content):
            if content is event["raw"]:
                raise UnsupportedConversion(f"No isolated content block for Markdown rendering: {event['id']}")
            parts.append(fenced(json.dumps(content, ensure_ascii=False, indent=2), "json"))
    return "\n\n".join(parts) + ("\n" if parts else "")


def _event_item(event):
    """Translate representable semantics; never silently flatten unknown records."""
    kind = event["kind"]
    raw = event["raw"]
    if event["harness"] == "codex" and raw.get("type") == "response_item":
        payload = raw.get("payload", {})
        if payload.get("type") in {"reasoning", "custom_tool_call", "custom_tool_call_output"}:
            return {key: value for key, value in payload.items()
                    if key not in {"id", "internal_chat_message_metadata_passthrough"}}
    if kind == "message" and event["role"] in {"user", "assistant", "system", "developer"}:
        # Native multimodal blocks require their own conversion, not text extraction.
        if _has_media(_content_record(event)):
            raise UnsupportedConversion(f"Multimodal conversion is unsupported: {event['id']}")
        content_type = "output_text" if event["role"] == "assistant" else "input_text"
        return {"type": "message", "role": event["role"],
                "content": [{"type": content_type, "text": event["text"]}]}
    if kind == "tool_call" and event["call_id"] and event["tool_name"]:
        args = event["arguments"]
        return {"type": "function_call", "call_id": event["call_id"], "name": event["tool_name"],
                "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}
    if kind == "tool_result" and event["call_id"]:
        output = event["text"]
        if event["status"] is not None:
            output = json.dumps({"status": event["status"], "text": output}, ensure_ascii=False)
        return {"type": "function_call_output", "call_id": event["call_id"], "output": output}
    if kind in {"reasoning", "thought", "thinking"}:
        return {"type": "reasoning", "summary": [{"type": "summary_text", "text": event["text"]}]}
    raise UnsupportedConversion(f"Cannot translate {kind!r} event {event['id']}; use canonical JSONL")


def native_control(event):
    """Known runtime records retained as provenance, not model conversation items."""
    raw = event["raw"]
    if event["harness"] != "codex" or event["kind"] != "metadata":
        return False
    if raw.get("type") in {"session_meta", "turn_context", "world_state", "token_usage_record"}:
        return True
    payload = raw.get("payload")
    return raw.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") in {
        "task_started", "task_complete", "task_completed", "turn_aborted", "token_count",
        "item_started", "item_completed", "user_message", "agent_message", "agent_reasoning",
        "thread_settings_applied",
    }


def codex_items(events):
    items = []
    calls = set()
    for event in records(events):
        if native_control(event):
            continue
        item = _event_item(event)
        if item["type"] in {"function_call", "custom_tool_call"}:
            if item["call_id"] in calls:
                raise UnsupportedConversion("Duplicate tool call IDs require an explicit identity mapping")
            calls.add(item["call_id"])
        elif item["type"] in {"function_call_output", "custom_tool_call_output"} and item["call_id"] not in calls:
            raise UnsupportedConversion("Tool result has no preceding call in the selected history")
        items.append(item)
    return items


@dataclass(frozen=True)
class Export:
    format: str
    text: str
    event_ids: tuple[str, ...]
    transformations: tuple[str, ...] = ()

    def save(self, path):
        path = Path(path)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(self.text)
        return path


def export_events(events, format="thearc"):
    events = records(events)
    ids = tuple(e["id"] for e in events)
    transformations = ()
    if format == "thearc":
        output = events
    elif format == "codex":
        items = iter(codex_items(events))
        output = [e["raw"] if native_control(e) else
                  {"type": "response_item", "timestamp": e["timestamp"], "payload": next(items)} for e in events]
        transformations = ("Events mapped to Responses items; source identities retained by export event_ids.",
                           "Tool results with a status use a JSON object containing status and original text.")
    elif format == "claude":
        if any(native_control(e) for e in events):
            raise UnsupportedConversion("Claude export cannot represent Codex lifecycle records")
        output = []
        previous = None
        for event, item in zip(events, codex_items(events), strict=True):
            kind = item["type"]
            if kind == "message":
                role = item["role"]
                if role not in {"user", "assistant"}:
                    raise UnsupportedConversion("Claude export does not support system/developer messages")
                content = [{"type": "text", "text": event["text"]}]
            elif kind == "function_call":
                role = "assistant"
                try:
                    args = json.loads(item["arguments"])
                except ValueError as exc:
                    raise UnsupportedConversion("Claude tool arguments must be JSON") from exc
                content = [{"type": "tool_use", "id": item["call_id"], "name": item["name"], "input": args}]
            elif kind == "function_call_output":
                role = "user"
                content = [{"type": "tool_result", "tool_use_id": item["call_id"],
                            "content": event["text"], "is_error": event["status"] in {"failure", "error"}}]
            else:
                raise UnsupportedConversion("Claude reasoning import requires native signed content")
            identifier = str(uuid5(NAMESPACE_URL, event["id"]))
            output.append({"type": role, "uuid": identifier, "parentUuid": previous,
                           "sessionId": event["session_id"], "timestamp": event["timestamp"],
                           "message": {"role": role, "content": content}})
            previous = identifier
        transformations = ("Normalized events mapped to Claude content blocks; native message IDs regenerated.",)
    else:
        raise UnsupportedConversion(f"Unsupported format: {format}")
    return Export(format, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in output), ids, transformations)


def immutable_events(events):
    return tuple(Record(e) for e in records(events))
