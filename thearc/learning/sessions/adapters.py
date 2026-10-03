"""Streaming provider normalization. Unknown records are retained as metadata."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from thearc.learning.sessions.models import Event, SourceConfig, SourceReference


def identity(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "input_text", "output_text"}
        )
    return ""


def timestamp(value: Any) -> str | None:
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value / 1000, UTC).isoformat()
    return value if isinstance(value, str) else None


def action(name: str | None) -> str | None:
    if not name:
        return None
    name = name.lower().rsplit("__", 1)[-1]
    return {
        "bash": "shell.exec",
        "exec_command": "shell.exec",
        "shell": "shell.exec",
        "shell_command": "shell.exec",
        "run_command": "shell.exec",
        "read": "file.read",
        "read_file": "file.read",
        "view_file": "file.read",
        "write": "file.write",
        "write_file": "file.write",
        "write_to_file": "file.write",
        "edit": "file.patch",
        "apply_patch": "file.patch",
        "replace_file_content": "file.patch",
        "grep": "search.text",
        "glob": "search.text",
        "search_web": "search.web",
        "spawn_agent": "agent.spawn",
        "invoke_subagent": "agent.spawn",
        "task": "agent.spawn",
        "agent": "agent.spawn",
        "send_message": "agent.message",
        "wait_agent": "agent.wait",
        "wait": "agent.wait",
        "interrupt_agent": "agent.cancel",
    }.get(name, "custom")


class HistoryAdapter(Protocol):
    def discover(self, source: SourceConfig) -> Iterator[Path]: ...
    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]: ...


class BaseAdapter:
    def discover(self, source: SourceConfig) -> Iterator[Path]:
        yield from sorted(source.root.rglob("*.jsonl"))

    def event(self, source: SourceConfig, ref: SourceReference, data: dict, meta: dict, **fields) -> Event:
        native_session = meta.get("session", ref.path.stem)
        session = identity(source.id, native_session)
        run = identity(session, meta.get("agent", "main"))
        return Event(
            id=identity(
                source.id,
                str(ref.path.relative_to(source.root)),
                ref.generation,
                ref.byte_offset,
                fields.pop("subrecord", 0),
            ),
            source_id=source.id,
            harness=source.harness,
            session_id=session,
            session_native_id=str(native_session) if native_session is not None else None,
            run_id=run,
            cwd=meta.get("cwd"),
            parent_run_id=meta.get("parent_run_id") or (identity(session, "main") if meta.get("agent") else None),
            native_id=data.get("id") or data.get("uuid"),
            parent_native_id=data.get("parentId"),
            timestamp=timestamp(data.get("timestamp")),
            native_type=data.get("type"),
            reference=ref,
            raw=data,
            **fields,
        )

    def blocks(self, source, ref, data, meta, message, pointer="/message/content") -> list[Event]:
        role = message.get("role")
        content = message.get("content", "")
        string_content = isinstance(content, str)
        result = []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            return result
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            fields: dict[str, Any] = {"kind": "message", "role": role, "subrecord": index}
            if kind in {"text", "input_text", "output_text"}:
                fields["text"] = str(block.get("text", ""))
            elif kind in {"toolCall", "tool_use"}:
                arguments = block.get("arguments", block.get("input", {}))
                fields.update(
                    kind="tool_call",
                    tool_name=block.get("name"),
                    action_kind=action(block.get("name")),
                    call_id=block.get("id"),
                    arguments=arguments,
                    text=json.dumps(arguments, ensure_ascii=False),
                )
            elif kind == "tool_result":
                fields.update(
                    kind="tool_result",
                    role="tool",
                    call_id=block.get("tool_use_id"),
                    text=text_content(block.get("content")),
                    status="failure" if block.get("is_error") else None,
                )
            elif kind in {"thinking", "reasoning"}:
                fields.update(kind="thinking", text=str(block.get("thinking", "")))
            else:
                fields.update(kind="unknown")
            block_ref = ref.model_copy(update={"json_pointer": pointer if string_content else f"{pointer}/{index}"})
            result.append(self.event(source, block_ref, data, meta, **fields))
        return result


class PiAdapter(BaseAdapter):
    def normalize(self, source, ref, data, metadata):
        if data.get("type") == "session":
            metadata.update(session=data.get("id", ref.path.stem), cwd=data.get("cwd"))
        message = data.get("message")
        if isinstance(message, dict):
            role = message.get("role")
            if role == "toolResult":
                return [
                    self.event(
                        source,
                        ref,
                        data,
                        metadata,
                        kind="tool_result",
                        role="tool",
                        text=text_content(message.get("content")),
                        call_id=message.get("toolCallId"),
                        tool_name=message.get("toolName"),
                        action_kind=action(message.get("toolName")),
                        status="failure" if message.get("isError") else None,
                    )
                ]
            if role == "bashExecution":
                code = message.get("exitCode")
                return [
                    self.event(
                        source,
                        ref,
                        data,
                        metadata,
                        kind="tool_result",
                        role="tool",
                        tool_name="bash",
                        action_kind="shell.exec",
                        arguments={"command": message.get("command")},
                        text=str(message.get("output", "")),
                        status="success" if code == 0 else "failure" if code is not None else None,
                    )
                ]
            return self.blocks(source, ref, data, metadata, message)
        kind = "compaction" if data.get("type") in {"compaction", "branch_summary"} else "metadata"
        return [self.event(source, ref, data, metadata, kind=kind, text=data.get("summary", ""))]


class ClaudeAdapter(BaseAdapter):
    def discover(self, source):
        for path in super().discover(source):
            if path.name != "history.jsonl" and "tool-results" not in path.parts:
                yield path

    def normalize(self, source, ref, data, metadata):
        child = "subagents" in ref.path.parts
        metadata.update(
            session=data.get("sessionId", metadata.get("session", ref.path.stem)),
            cwd=data.get("cwd", metadata.get("cwd")),
        )
        if child:
            # The parent directory is the session ID; agent IDs are run IDs, not sessions.
            position = ref.path.parts.index("subagents")
            metadata.update(session=ref.path.parts[position - 1], agent=ref.path.stem)
        message = data.get("message")
        if isinstance(message, dict):
            return self.blocks(source, ref, data, metadata, message)
        return [self.event(source, ref, data, metadata, kind="metadata")]


class CodexAdapter(BaseAdapter):
    def discover(self, source):
        yield from sorted(source.root.rglob("rollout-*.jsonl"))

    def normalize(self, source, ref, data, metadata):
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            return [self.event(source, ref, data, metadata, kind="unknown")]
        if data.get("type") == "session_meta":
            metadata.update(session=payload.get("id", payload.get("session_id", ref.path.stem)), cwd=payload.get("cwd"))
            lineage = payload.get("source")
            if isinstance(lineage, dict):
                subagent = lineage.get("subagent", {})
                if isinstance(subagent, dict):
                    spawned = subagent.get("thread_spawn", {})
                    if isinstance(spawned, dict) and spawned.get("parent_thread_id"):
                        metadata["parent_run_id"] = identity(identity(source.id, spawned["parent_thread_id"]), "main")
        if data.get("type") == "response_item":
            kind = payload.get("type")
            if kind == "message":
                return self.blocks(source, ref, data, metadata, payload, "/payload/content")
            if kind in {"function_call", "custom_tool_call"}:
                arguments = payload.get("arguments", payload.get("input", ""))
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        pass
                return [
                    self.event(
                        source,
                        ref,
                        data,
                        metadata,
                        kind="tool_call",
                        role="assistant",
                        tool_name=payload.get("name"),
                        action_kind=action(payload.get("name")),
                        arguments=arguments,
                        text=json.dumps(arguments, ensure_ascii=False),
                        call_id=payload.get("call_id"),
                    )
                ]
            if kind in {"function_call_output", "custom_tool_call_output"}:
                output = payload.get("output", "")
                return [
                    self.event(
                        source,
                        ref,
                        data,
                        metadata,
                        kind="tool_result",
                        role="tool",
                        call_id=payload.get("call_id"),
                        text=output if isinstance(output, str) else json.dumps(output),
                    )
                ]
        kind = "compaction" if data.get("type") == "compacted" else "metadata"
        # event_msg often mirrors response_item: retain it but exclude from default search.
        return [
            self.event(
                source,
                ref,
                data,
                metadata,
                kind=kind,
                text=str(payload.get("message", "")) if kind == "compaction" else "",
            )
        ]


class AntigravityAdapter(BaseAdapter):
    def discover(self, source: SourceConfig) -> Iterator[Path]:
        yield from sorted(source.root.rglob("transcript.jsonl"))

    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]:
        session_id = data.get("conversation_id") or metadata.get("session")
        if not session_id:
            for part in reversed(ref.path.parts[:-1]):
                if part not in {".system_generated", "logs"}:
                    session_id = part
                    break
            if not session_id:
                session_id = ref.path.parent.name
            metadata["session"] = session_id

        step_type = data.get("type")
        events: list[Event] = []

        if step_type == "USER_INPUT":
            content = data.get("content", "")
            events.append(
                self.event(
                    source,
                    ref,
                    data,
                    metadata,
                    kind="message",
                    role="user",
                    text=text_content(content),
                )
            )
        elif step_type == "PLANNER_RESPONSE":
            if data.get("thinking"):
                events.append(
                    self.event(
                        source,
                        ref,
                        data,
                        metadata,
                        kind="thinking",
                        role="assistant",
                        text=str(data["thinking"]),
                        subrecord=0,
                    )
                )
            if data.get("content"):
                events.append(
                    self.event(
                        source,
                        ref,
                        data,
                        metadata,
                        kind="message",
                        role="assistant",
                        text=text_content(data["content"]),
                        subrecord=1,
                    )
                )
            tool_calls = data.get("tool_calls") or []
            for idx, call in enumerate(tool_calls):
                tool_name = call.get("name")
                args = call.get("args") or call.get("arguments", {})
                call_id = call.get("id") or f"{ref.path.stem}-{data.get('step_index')}-{idx}"
                events.append(
                    self.event(
                        source,
                        ref,
                        data,
                        metadata,
                        kind="tool_call",
                        role="assistant",
                        tool_name=tool_name,
                        action_kind=action(tool_name),
                        call_id=call_id,
                        arguments=args,
                        text=json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args),
                        subrecord=10 + idx,
                    )
                )
        elif step_type in {"GENERIC", "TOOL_OUTPUT"}:
            status = "failure" if data.get("status") == "ERROR" else "success"
            content = data.get("content", "")
            events.append(
                self.event(
                    source,
                    ref,
                    data,
                    metadata,
                    kind="tool_result",
                    role="tool",
                    status=status,
                    text=text_content(content),
                )
            )
        else:
            events.append(self.event(source, ref, data, metadata, kind="metadata"))

        return events


ADAPTERS: dict[str, HistoryAdapter] = {
    "pi": PiAdapter(),
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "antigravity": AntigravityAdapter(),
}


class DataClawAdapter(BaseAdapter):
    """Import exported sessions; retain per-message evidence, not N copies of a session."""

    version = "dataclaw-1"

    def normalize(self, source, ref, data, metadata):
        messages = data.get("messages")
        if not isinstance(messages, list) or not data.get("session_id"):
            raise ValueError("DataClaw records require session_id and a messages list")
        metadata.update(session=data["session_id"], cwd=None)
        events = []

        def append(raw, pointer, **fields):
            event_ref = ref.model_copy(update={"json_pointer": pointer})
            events.append(
                self.event(source, event_ref, raw, metadata, subrecord=len(events), raw_pointer=pointer, **fields)
            )

        for message_index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise TypeError("DataClaw messages must be objects")
            pointer = f"/messages/{message_index}"
            content = text_content(message.get("content"))
            if content:
                append(message, pointer, kind="message", role=message.get("role"), text=content)
            if message.get("thinking"):
                append(message, pointer, kind="thinking", role=message.get("role"), text=str(message["thinking"]))
            tools = message.get("tool_uses") or []
            if not isinstance(tools, list):
                raise TypeError("DataClaw tool_uses must be a list")
            for tool_index, tool in enumerate(tools):
                if not isinstance(tool, dict):
                    raise TypeError("DataClaw tool entries must be objects")
                tool_pointer = f"{pointer}/tool_uses/{tool_index}"
                name = tool.get("tool")
                call = tool.get("id") or f"imported:{ref.byte_offset}:{message_index}:{tool_index}"
                arguments = tool.get("input")
                append(
                    tool,
                    tool_pointer,
                    kind="tool_call",
                    role="assistant",
                    tool_name=name,
                    action_kind=action(name),
                    call_id=call,
                    arguments=arguments,
                    text=json.dumps(arguments, ensure_ascii=False),
                )
                if "output" in tool and tool["output"] is not None:
                    output = tool["output"]
                    status = tool.get("status")
                    if isinstance(output, dict):
                        exit_code = output.get("exit_code")
                        if isinstance(exit_code, int):
                            status = "success" if exit_code == 0 else "failure"
                        if isinstance(output.get("text"), str):
                            output = output["text"]
                        elif isinstance(output.get("output"), str):
                            output = output["output"]
                    append(
                        tool,
                        tool_pointer,
                        kind="tool_result",
                        role="tool",
                        tool_name=name,
                        action_kind=action(name),
                        call_id=call,
                        status=status if status in {"success", "failure"} else None,
                        text=output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
                    )
        if not events:
            # Retain an empty session without duplicating a large messages array.
            append(data, "", kind="metadata")
        return events


def get_adapter(source: SourceConfig) -> HistoryAdapter:
    return DataClawAdapter() if source.format == "dataclaw" else ADAPTERS[source.harness]
