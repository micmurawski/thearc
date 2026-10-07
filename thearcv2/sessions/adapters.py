"""Streaming provider normalization. Unknown records are retained as metadata."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from thearcv2.sessions.models import Event, SourceConfig, SourceReference


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


class TranscriptAdapter(Protocol):
    def discover(self, source: SourceConfig) -> Iterator[Path]: ...
    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]: ...


class BaseAdapter:
    def discover(self, source: SourceConfig) -> Iterator[Path]:
        yield from sorted(source.root.rglob("*.jsonl"))

    def event(self, source: SourceConfig, ref: SourceReference, data: dict, meta: dict, **fields: Any) -> Event:
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
            timestamp=timestamp(fields.pop("timestamp", data.get("timestamp"))),
            native_type=data.get("type"),
            reference=ref,
            raw=data,
            **fields,
        )

    def blocks(self, source: SourceConfig, ref: SourceReference, data: dict, meta: dict, message: dict,
               pointer: str = "/message/content") -> list[Event]:
        role = message.get("role")
        content = message.get("content", "")
        string_content = isinstance(content, str)
        result: list[Event] = []
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


class OpenCodeAdapter(BaseAdapter):
    """Lossless OpenCode export envelopes, stored as one JSON object per line."""

    version = "opencode-2"

    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]:
        info = data.get("info", {})
        if not isinstance(info, dict) or not info.get("id") or not isinstance(data.get("messages"), list):
            raise ValueError("Expected OpenCode export with info and messages")
        metadata.update(session=info["id"], cwd=info.get("location", {}).get("directory", info.get("directory")))
        result = [self.event(source, ref, info, metadata, kind="metadata", raw_pointer="/info",
                             timestamp=info.get("time", {}).get("created"))]
        for m, message in enumerate(data["messages"]):
            if not isinstance(message, dict):
                raise TypeError("Expected OpenCode message with info")
            native = "type" in message
            message_info = message if native else message.get("info", {})
            role = message.get("type") if native else message_info.get("role")
            content_key = "content" if native else "parts"
            parts = message.get(content_key) if role == "assistant" or not native else [message]
            if not isinstance(parts, list):
                raise TypeError("Expected OpenCode message parts")
            for p, part in enumerate(parts):
                if not isinstance(part, dict):
                    raise TypeError("Expected OpenCode part object")
                pointer = f"/messages/{m}"
                if role == "assistant" or not native:
                    pointer += f"/{content_key}/{p}"
                part_ref = ref.model_copy(update={"json_pointer": pointer})
                fields = {"role": role if role in {"assistant", "user", "system"} else None,
                          "kind": "metadata", "text": "",
                          "raw_pointer": part_ref.json_pointer,
                          "timestamp": message_info.get("time", {}).get("created")}
                if part.get("type") in {"text", "reasoning", "user", "system"}:
                    fields.update(kind="thinking" if part["type"] == "reasoning" else "message",
                                  text=part.get("text", ""))
                elif part.get("type") == "tool":
                    state = part.get("state", {})
                    name = part.get("name") if native else part.get("tool")
                    fields.update(kind="tool_call", tool_name=name,
                                  action_kind=action(name), call_id=part.get("id") if native else part.get("callID"),
                                  arguments=state.get("input", {}), text=json.dumps(state.get("input", {})))
                    result.append(self.event(source, part_ref, part, metadata, subrecord=len(result), **fields))
                    if state.get("status") not in {"completed", "error"}:
                        continue
                    output = text_content(state.get("content")) if native else state.get("output", "")
                    error = state.get("error")
                    if isinstance(error, dict):
                        error = error.get("message", json.dumps(error))
                    exit_code = state.get("metadata", {}).get("exit")
                    failed = state["status"] == "error" or (type(exit_code) is int and exit_code != 0)
                    fields.update(kind="tool_result", role="tool", arguments=None,
                                  status="failure" if failed else "success",
                                  text=str(error or output))
                result.append(self.event(source, part_ref, part, metadata, subrecord=len(result), **fields))
        return result


class PiAdapter(BaseAdapter):
    version = "pi-2"

    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]:
        if data.get("type") == "session":
            metadata.update(session=data.get("id", ref.path.stem), cwd=data.get("cwd"))
        message = data.get("message")
        if isinstance(message, dict):
            role = message.get("role")
            if role == "system":
                # Pi records the system prompt it sent (sections, tools) — configuration, not conversation.
                return [self.event(source, ref, data, metadata, kind="metadata", role="system",
                                   text=text_content(message.get("content")))]
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
    def discover(self, source: SourceConfig) -> Iterator[Path]:
        for path in super().discover(source):
            if path.name != "history.jsonl" and "tool-results" not in path.parts:
                yield path

    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]:
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
    version = "codex-3"

    @staticmethod
    def result_text(output: Any) -> str:
        """Readable output: text blocks of a content list, without blocks that only report an exit code."""
        if not isinstance(output, list):
            return output if isinstance(output, str) else json.dumps(output)
        parts = []
        for block in output:
            if not isinstance(block, dict) or block.get("type") not in {"text", "input_text", "output_text"}:
                continue
            text = str(block.get("text", ""))
            try:
                envelope = json.loads(text)
            except ValueError:
                envelope = None
            if isinstance(envelope, dict) and set(envelope) == {"exit_code"}:
                continue
            parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def result_status(output: Any) -> str | None:
        """Read explicit exit codes from tool envelopes, never error words in arbitrary output."""
        values = output if isinstance(output, list) else [output]
        codes = []
        for value in values:
            if isinstance(value, dict) and value.get("type") in {"text", "input_text", "output_text"}:
                value = value.get("text")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    continue
            if isinstance(value, dict) and "exit_code" in value:
                codes.append(value["exit_code"])
        if any(type(code) is int and code != 0 for code in codes):
            return "failure"
        if codes and all(type(code) is int and code == 0 for code in codes):
            return "success"
        return None

    def discover(self, source: SourceConfig) -> Iterator[Path]:
        yield from sorted(source.root.rglob("rollout-*.jsonl"))

    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]:
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
                        text=self.result_text(output),
                        status=self.result_status(output),
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
    """Antigravity ``transcript.jsonl`` steps.

    Tool calls carry no IDs: each ``PLANNER_RESPONSE`` lists its calls, and the
    steps that follow report their results in the same order, typed by tool
    (``VIEW_FILE``, ``RUN_COMMAND``, ``CODE_ACTION``, ``GENERIC``, …). Calls waiting
    for a result are kept in the per-file state, so incremental syncs keep pairing.
    """

    version = "antigravity-2"
    _NOT_RESULTS = frozenset({"USER_INPUT", "PLANNER_RESPONSE", "SYSTEM_MESSAGE", "CHECKPOINT",
                              "CONVERSATION_HISTORY", "ERROR_MESSAGE"})

    def discover(self, source: SourceConfig) -> Iterator[Path]:
        yield from sorted(source.root.rglob("transcript.jsonl"))

    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]:
        if not metadata.get("session"):
            session_id = data.get("conversation_id")
            if not session_id:
                parts = [part for part in ref.path.parts[:-1] if part not in {".system_generated", "logs"}]
                session_id = parts[-1] if parts else ref.path.parent.name
            metadata["session"] = session_id
        if data.get("cwd"):
            metadata["cwd"] = data["cwd"]
        pending: list = metadata.setdefault("_pending_calls", [])
        step_type = data.get("type")
        content = text_content(data.get("content") or "")
        timestamp = data.get("created_at")

        if step_type == "USER_INPUT":
            pending.clear()  # A new prompt ends the previous response; unanswered calls stay unanswered.
            return [self.event(source, ref, data, metadata, kind="message", role="user", text=content,
                               timestamp=timestamp)]
        if step_type == "SYSTEM_MESSAGE":
            return [self.event(source, ref, data, metadata, kind="message", role="system", text=content,
                               timestamp=timestamp)]
        if step_type == "CHECKPOINT":
            return [self.event(source, ref, data, metadata, kind="compaction", text=content, timestamp=timestamp)]
        if step_type == "PLANNER_RESPONSE":
            pending.clear()
            events = []
            if data.get("thinking"):
                events.append(self.event(source, ref, data, metadata, kind="thinking", role="assistant",
                                         text=str(data["thinking"]), timestamp=timestamp, subrecord=0))
            if content:
                events.append(self.event(source, ref, data, metadata, kind="message", role="assistant",
                                         text=content, timestamp=timestamp, subrecord=1))
            for index, call in enumerate(data.get("tool_calls") or []):
                name = call.get("name")
                args = call.get("args", call.get("arguments", {}))
                call_id = call.get("id") or f"{metadata['session']}-{data.get('step_index')}-{index}"
                pending.append([call_id, name])
                events.append(self.event(
                    source, ref, data, metadata, kind="tool_call", role="assistant", tool_name=name,
                    action_kind=action(name), call_id=call_id, arguments=args, timestamp=timestamp,
                    subrecord=10 + index,
                    text=json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)))
            return events
        if step_type not in self._NOT_RESULTS or (step_type == "ERROR_MESSAGE" and pending):
            call_id, name = pending.pop(0) if pending else (None, None)
            exit_code = data.get("exit_code")
            if data.get("status") == "ERROR" or data.get("error") or (type(exit_code) is int and exit_code != 0):
                status = "failure"
            elif data.get("status") == "RUNNING":
                status = None
            else:
                status = "success"
            return [self.event(source, ref, data, metadata, kind="tool_result", role="tool", call_id=call_id,
                               tool_name=name, action_kind=action(name), status=status,
                               text=content or str(data.get("error") or ""), timestamp=timestamp)]
        return [self.event(source, ref, data, metadata, kind="metadata", text=str(data.get("error") or ""),
                           timestamp=timestamp)]


ADAPTERS: dict[str, TranscriptAdapter] = {
    "opencode": OpenCodeAdapter(),
    "pi": PiAdapter(),
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "antigravity": AntigravityAdapter(),
}


class DataClawAdapter(BaseAdapter):
    """Import exported sessions; retain per-message evidence, not N copies of a session."""

    version = "dataclaw-1"

    def normalize(self, source: SourceConfig, ref: SourceReference, data: dict, metadata: dict) -> list[Event]:
        messages = data.get("messages")
        if not isinstance(messages, list) or not data.get("session_id"):
            raise ValueError("DataClaw records require session_id and a messages list")
        metadata.update(session=data["session_id"], cwd=None)
        events: list[Event] = []

        def append(raw: dict, pointer: str, **fields: Any) -> None:
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


def get_adapter(source: SourceConfig) -> TranscriptAdapter:
    return DataClawAdapter() if source.format == "dataclaw" else ADAPTERS[source.harness]
