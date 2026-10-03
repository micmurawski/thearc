"""Record builders: serialize TextMessages into native provider JSONL/JSON records.

Each builder provides a ``build(session_id, cwd, timestamp, messages)`` method
that returns the ``list[JsonObject]`` to write via the target store.

Supported builders:
    - ``CodexRecordBuilder``       — Codex rollout JSONL
    - ``PiRecordBuilder``          — Pi JSONL append-only
    - ``ClaudeRecordBuilder``      — Claude Code JSONL transcript
    - ``OpenCodeRecordBuilder``    — OpenCode export JSON
    - ``AntigravityRecordBuilder`` — Antigravity transcript.jsonl (simplified)
"""

from __future__ import annotations

from uuid import uuid4

from .models import (
    JsonObject,
    TextMessage,
    iso_to_epoch_ms,
    now_iso,
    opencode_id,
    opencode_slug,
)


class CodexRecordBuilder:
    """Build Codex-format JSONL rollout records."""

    def build(
        self,
        session_id: str,
        cwd: str,
        timestamp: str,
        messages: list[TextMessage],
    ) -> list[JsonObject]:
        ts = timestamp or now_iso()
        records: list[JsonObject] = [
            {
                "type": "session_meta",
                "timestamp": ts,
                "payload": {
                    "id": session_id,
                    "timestamp": ts,
                    "cwd": cwd,
                },
            }
        ]
        for msg in messages:
            content: object
            if msg.is_compaction:
                content = [{"type": "text", "text": msg.text}]
                records.append(
                    {
                        "type": "task_complete",
                        "timestamp": msg.timestamp or ts,
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": content,
                            "replacement_history": [],
                        },
                    }
                )
                continue
            content = [{"type": "text", "text": msg.text}]
            entry: JsonObject = {
                "type": "message",
                "timestamp": msg.timestamp or ts,
                "payload": {
                    "type": "message",
                    "role": msg.role,
                    "content": content,
                },
            }
            if msg.model:
                entry["payload"]["model"] = msg.model  # type: ignore[index]
            records.append(entry)
        return records


class PiRecordBuilder:
    """Build Pi-format JSONL append-only records."""

    def build(
        self,
        session_id: str,
        cwd: str,
        timestamp: str,
        messages: list[TextMessage],
    ) -> list[JsonObject]:
        ts = timestamp or now_iso()
        records: list[JsonObject] = [
            {
                "type": "session_meta",
                "timestamp": ts,
                "payload": {
                    "id": session_id,
                    "timestamp": ts,
                    "cwd": cwd,
                },
            }
        ]
        for msg in messages:
            if msg.is_compaction:
                records.append(
                    {
                        "type": "compaction",
                        "timestamp": msg.timestamp or ts,
                        "summary": msg.text,
                    }
                )
                continue
            message_record: JsonObject = {
                "type": "message",
                "timestamp": msg.timestamp or ts,
                "message": {
                    "role": msg.role,
                    "content": msg.text,
                },
            }
            if msg.model:
                message_record["message"]["model"] = msg.model  # type: ignore[index]
            if msg.provider:
                message_record["message"]["provider"] = msg.provider  # type: ignore[index]
            if msg.api:
                message_record["message"]["api"] = msg.api  # type: ignore[index]
            records.append(message_record)
        return records


class ClaudeRecordBuilder:
    """Build Claude Code-format JSONL transcript records."""

    def build(
        self,
        session_id: str,
        cwd: str,
        timestamp: str,
        messages: list[TextMessage],
    ) -> list[JsonObject]:
        ts = timestamp or now_iso()
        records: list[JsonObject] = []
        for msg in messages:
            if msg.is_compaction:
                records.append(
                    {
                        "type": "system",
                        "subtype": "compact_boundary",
                        "timestamp": msg.timestamp or ts,
                        "content": msg.text,
                        "cwd": cwd,
                        "sessionId": session_id,
                    }
                )
                continue
            content: object
            content = [{"type": "text", "text": msg.text}]
            record: JsonObject = {
                "type": msg.role,
                "timestamp": msg.timestamp or ts,
                "cwd": cwd,
                "sessionId": session_id,
                "message": {
                    "id": f"msg_{uuid4().hex[:24]}",
                    "role": msg.role,
                    "content": content,
                    "stop_reason": "end_turn",
                    "type": "message",
                },
            }
            if msg.model:
                record["message"]["model"] = msg.model  # type: ignore[index]
            records.append(record)
        return records


class OpenCodeRecordBuilder:
    """Build OpenCode export JSON records."""

    def build(
        self,
        session_id: str,
        cwd: str,
        timestamp: str,
        messages: list[TextMessage],
    ) -> list[JsonObject]:
        ts = timestamp or now_iso()
        opencode_messages: list[JsonObject] = []
        for i, msg in enumerate(messages):
            msg_ts = msg.timestamp or ts
            msg_id = opencode_id("msg", msg_ts)
            info: JsonObject = {
                "id": msg_id,
                "role": msg.role,
                "time": iso_to_epoch_ms(msg_ts),
            }
            if msg.model:
                info["modelID"] = msg.model
            if msg.provider:
                info["providerID"] = msg.provider
            if msg.is_compaction:
                info["summary"] = True
            parts: list[JsonObject] = [{"type": "text", "text": msg.text}]
            opencode_messages.append({"info": info, "parts": parts})

        export_obj: JsonObject = {
            "id": session_id,
            "title": opencode_slug(messages[0].text if messages else "imported-session"),
            "cwd": cwd,
            "createdAt": ts,
            "messages": opencode_messages,
        }
        return [export_obj]


class AntigravityRecordBuilder:
    """Build Antigravity-compatible transcript.jsonl records.

    Output follows the agy transcript JSONL step schema used by the
    ``thearc.learning.sessions`` SessionStore.
    """

    def build(
        self,
        session_id: str,
        cwd: str,
        timestamp: str,
        messages: list[TextMessage],
    ) -> list[JsonObject]:
        ts = timestamp or now_iso()
        records: list[JsonObject] = []
        for i, msg in enumerate(messages):
            msg_ts = msg.timestamp or ts
            source = "USER_EXPLICIT" if msg.role == "user" else "MODEL"
            step_type = "USER_INPUT" if msg.role == "user" else "PLANNER_RESPONSE"
            content: object
            if msg.role == "user":
                content = msg.text
            else:
                content = [{"type": "text", "text": msg.text}]
            record: JsonObject = {
                "step_index": i,
                "source": source,
                "type": step_type,
                "status": "DONE",
                "created_at": msg_ts,
                "content": content,
            }
            if cwd:
                record["cwd"] = cwd
            records.append(record)
        return records


class MarkdownRecordBuilder:
    """Build a Markdown document representing the session.

    Outputs a single record containing a ``markdown`` key with the full document text,
    ready to be written by the MarkdownStore.
    """

    def build(
        self,
        session_id: str,
        cwd: str,
        timestamp: str,
        messages: list[TextMessage],
    ) -> list[JsonObject]:
        ts = timestamp or now_iso()
        lines: list[str] = []
        
        # Frontmatter
        lines.append("---")
        lines.append(f"id: {session_id}")
        if cwd:
            lines.append(f"cwd: {cwd}")
        lines.append(f"timestamp: {ts}")
        lines.append("---\n")
        
        lines.append(f"# Session {session_id}\n")

        for msg in messages:
            if msg.is_compaction:
                lines.append("## System (Compaction)\n")
                quoted = "\n".join(f"> {line}" for line in msg.text.splitlines())
                lines.append(f"{quoted}\n")
            elif msg.is_contextual:
                lines.append("## System (Context)\n")
                lines.append(f"{msg.text}\n")
            else:
                role_title = "User" if msg.role == "user" else "Assistant"
                if msg.model:
                    lines.append(f"## {role_title} ({msg.model})\n")
                else:
                    lines.append(f"## {role_title}\n")
                lines.append(f"{msg.text}\n")

        return [{"markdown": "\n".join(lines)}]
