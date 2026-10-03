"""Message extractor: pull provider-neutral TextMessage objects from native sessions.

Each ``from_<provider>`` method accepts a ``NativeSession`` and returns a list
of ``TextMessage`` objects that can then be fed to any record builder.

Supported sources:
    - ``from_codex``      — Codex CLI JSONL rollout files
    - ``from_pi``         — Pi agent JSONL append-only files
    - ``from_claude``     — Claude Code JSONL transcript files
    - ``from_opencode``   — OpenCode export JSON files
    - ``from_agy``        — Antigravity transcript.jsonl brain files
"""

from __future__ import annotations

from typing import ClassVar

from .models import (
    JsonObject,
    NativeSession,
    TextMessage,
    _as_list,
    _as_object,
    _string_value,
)


class MessageExtractor:
    """Extract provider-neutral messages from native sessions."""

    _ROLE_MAP: ClassVar[dict[str, str]] = {"assistant": "assistant", "user": "user", "system": "user"}

    # -----------------------------------------------------------------------
    # Codex
    # -----------------------------------------------------------------------

    def from_codex(self, session: NativeSession) -> list[TextMessage]:
        messages: list[TextMessage] = []
        for record in session.records:
            if record.get("type") == "task_complete":
                # Replacement history (compaction boundary)
                payload = _as_object(record.get("payload"))
                if payload is None:
                    continue
                rh = _as_list(payload.get("replacement_history"))
                if rh:
                    for item in rh:
                        item_obj = _as_object(item)
                        if item_obj is None or item_obj.get("type") != "message":
                            continue
                        msg = self._extract_codex_message(item_obj, record.get("timestamp", session.timestamp))
                        if msg:
                            messages.append(msg)
                continue
            payload = _as_object(record.get("payload"))
            if payload is None or payload.get("type") != "message":
                continue
            timestamp = _string_value(record, "timestamp") or session.timestamp
            msg = self._extract_codex_message(payload, timestamp)
            if msg:
                messages.append(msg)
        return messages

    @staticmethod
    def _extract_codex_message(payload: JsonObject, timestamp: object) -> TextMessage | None:
        role = _string_value(payload, "role")
        if role not in {"user", "assistant"}:
            return None
        content = payload.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for part in content:
                part_obj = _as_object(part)
                if part_obj is None:
                    continue
                if part_obj.get("type") == "text":
                    t = _string_value(part_obj, "text") or ""
                    if t:
                        parts.append(t)
            text = "\n".join(parts)
        else:
            return None
        if not text:
            return None
        ts = timestamp if isinstance(timestamp, str) else ""
        model = _string_value(payload, "model")
        return TextMessage(role, text, ts, model=model, provider="codex")

    # -----------------------------------------------------------------------
    # Pi
    # -----------------------------------------------------------------------

    def from_pi(self, session: NativeSession) -> list[TextMessage]:
        messages: list[TextMessage] = []
        current_provider: str | None = None
        current_model: str | None = None
        for record in session.records:
            rtype = record.get("type")
            if rtype == "model_change":
                current_provider = _string_value(record, "provider") or current_provider
                current_model = _string_value(record, "modelId") or current_model
                continue
            if rtype == "compaction":
                summary = _string_value(record, "summary") or ""
                timestamp = _string_value(record, "timestamp") or session.timestamp
                if summary:
                    messages.append(TextMessage("user", summary, timestamp, is_compaction=True))
                continue
            if rtype != "message":
                continue
            message = _as_object(record.get("message"))
            if message is None:
                continue
            role = _string_value(message, "role")
            if role not in {"user", "assistant", "system"}:
                continue
            text = self._content_text(message.get("content"))
            if not text:
                continue
            timestamp = (
                _string_value(record, "timestamp")
                or _string_value(message, "timestamp")
                or session.timestamp
            )
            mapped_role = self._ROLE_MAP.get(role, role)
            messages.append(
                TextMessage(
                    mapped_role,
                    text,
                    timestamp,
                    model=_string_value(message, "model") or current_model,
                    provider=_string_value(message, "provider") or current_provider,
                    api=_string_value(message, "api"),
                )
            )
        return messages

    # -----------------------------------------------------------------------
    # Claude
    # -----------------------------------------------------------------------

    def from_claude(self, session: NativeSession) -> list[TextMessage]:
        messages: list[TextMessage] = []
        for record in session.records:
            rtype = record.get("type")
            # Compaction boundary
            if rtype == "system" and record.get("subtype") == "compact_boundary":
                summary = _string_value(record, "content") or ""
                timestamp = _string_value(record, "timestamp") or session.timestamp
                if summary:
                    messages.append(TextMessage("user", summary, timestamp, is_compaction=True))
                continue
            if rtype not in ("user", "assistant"):
                continue
            message = _as_object(record.get("message"))
            if message is None:
                continue
            role = _string_value(message, "role") or rtype
            if role not in ("user", "assistant"):
                continue
            text = self._content_text(message.get("content"))
            if not text:
                continue
            timestamp = _string_value(record, "timestamp") or session.timestamp
            model = _string_value(message, "model")
            if record.get("isCompactSummary") is True:
                messages.append(TextMessage("user", text, timestamp, is_compaction=True))
                continue
            messages.append(TextMessage(role, text, timestamp, model=model, provider="claude"))
        return messages

    # -----------------------------------------------------------------------
    # OpenCode
    # -----------------------------------------------------------------------

    def from_opencode(self, session: NativeSession) -> list[TextMessage]:
        if not session.records:
            return []
        export = session.records[0]
        raw_messages = _as_list(export.get("messages")) or []
        extracted: list[TextMessage] = []
        for item in raw_messages:
            message = _as_object(item)
            if message is None:
                continue
            info = _as_object(message.get("info"))
            if info is None:
                continue
            role = _string_value(info, "role")
            if role not in {"user", "assistant"}:
                continue
            parts = _as_list(message.get("parts")) or []
            text = self._opencode_parts_text(parts)
            timestamp = self._opencode_timestamp(info) or session.timestamp
            if info.get("summary") is True and role == "assistant" and text:
                extracted.append(TextMessage("user", text, timestamp, is_compaction=True))
                continue
            if not text:
                continue
            extracted.append(
                TextMessage(
                    role,
                    text,
                    timestamp,
                    model=_string_value(info, "modelID"),
                    provider=_string_value(info, "providerID"),
                )
            )
        return extracted

    # -----------------------------------------------------------------------
    # Antigravity (agy)
    # -----------------------------------------------------------------------

    def from_agy(self, session: NativeSession) -> list[TextMessage]:
        """Extract messages from an Antigravity transcript.jsonl file.

        Antigravity transcript steps have a ``source`` field
        (``USER_EXPLICIT``, ``MODEL``, ``SYSTEM``) and a ``content`` field
        with the text payload.
        """
        messages: list[TextMessage] = []
        for record in session.records:
            source = record.get("source", "")
            step_type = record.get("type", "")
            timestamp = _string_value(record, "created_at") or session.timestamp
            content = record.get("content")

            if step_type == "USER_INPUT" or source == "USER_EXPLICIT":
                text = self._agy_content_text(content)
                if text:
                    messages.append(TextMessage("user", text, timestamp, provider="agy"))
            elif step_type == "PLANNER_RESPONSE" or source == "MODEL":
                text = self._agy_content_text(content)
                if text:
                    messages.append(TextMessage("assistant", text, timestamp, provider="agy"))
        return messages

    # -----------------------------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _content_text(content: object) -> str:
        """Extract text from Claude/Pi-style content (str or list of blocks)."""
        if isinstance(content, str):
            return content
        parts_list = _as_list(content)
        if parts_list is None:
            return ""
        parts: list[str] = []
        for part in parts_list:
            part_obj = _as_object(part)
            if part_obj is None:
                if isinstance(part, str):
                    parts.append(part)
                continue
            if part_obj.get("type") == "text":
                t = _string_value(part_obj, "text") or ""
                if t:
                    parts.append(t)
        return "\n".join(parts)

    @staticmethod
    def _opencode_parts_text(parts: list) -> str:
        texts: list[str] = []
        for part in parts:
            part_obj = _as_object(part)
            if part_obj is None:
                continue
            ptype = part_obj.get("type")
            if ptype == "text":
                t = _string_value(part_obj, "text") or ""
                if t:
                    texts.append(t)
            elif ptype == "tool-result":
                content = _as_list(part_obj.get("content")) or []
                for c in content:
                    c_obj = _as_object(c)
                    if c_obj and c_obj.get("type") == "text":
                        t = _string_value(c_obj, "text") or ""
                        if t:
                            texts.append(t)
        return "\n".join(texts)

    @staticmethod
    def _opencode_timestamp(info: JsonObject) -> str | None:
        ms = info.get("time")
        if isinstance(ms, (int, float)):
            from .models import epoch_ms_to_iso
            return epoch_ms_to_iso(int(ms))
        return _string_value(info, "createdAt")

    @staticmethod
    def _agy_content_text(content: object) -> str:
        """Extract text from Antigravity content (string, list, or dict)."""
        if isinstance(content, str):
            return content
        content_list = _as_list(content)
        if content_list is not None:
            parts: list[str] = []
            for part in content_list:
                part_obj = _as_object(part)
                if part_obj is None:
                    if isinstance(part, str):
                        parts.append(part)
                    continue
                t = _string_value(part_obj, "text") or _string_value(part_obj, "content") or ""
                if t:
                    parts.append(t)
            return "\n".join(parts)
        content_obj = _as_object(content)
        if content_obj is not None:
            return _string_value(content_obj, "text") or _string_value(content_obj, "content") or ""
        return ""
