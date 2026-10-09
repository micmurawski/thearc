"""Literal command matching across harnesses, without executing or interpreting scripts."""

from __future__ import annotations

import ast
import json
import re
import shlex
from typing import Any

from thearcv2.sessions.models import Event

from .patterns import BehavioralPattern, PatternMatch, _stream_key

_SHELL_TOOLS = {"bash", "shell", "exec_command", "shell_command", "run_command"}
_COMMAND_KEYS = ("command", "cmd", "CommandLine", "script")
# Tokenize literals and comments as single units so quoted examples do not become calls.
_JS_TOKENS = re.compile(
    r'''//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|'''
    r"`(?:\\[\s\S]|[^`\\])*`|[A-Za-z_$][\w$]*|[^\s]"
)


def _name(tool: str | None) -> str:
    return (tool or "").lower().rsplit("__", 1)[-1].rsplit(".", 1)[-1]


def _script(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(part, str) for part in value):
        # Preserve the script supplied to bash -c / zsh -lc instead of its shell quoting.
        if value[0].rsplit("/", 1)[-1] in {"bash", "sh", "zsh", "dash", "ksh"}:
            for index, part in enumerate(value[1:-1], 1):
                if not part.startswith("-") or part == "--":
                    break
                if part.startswith("-") and not part.startswith("--") and "c" in part[1:]:
                    return str(value[index + 1])
        return shlex.join(value)
    return None


def _literal(token: str) -> str | None:
    if token.startswith("`"):
        # Template interpolation depends on runtime values we cannot reconstruct.
        if "${" in token or "\\" in token:
            return None
        return token[1:-1]
    if token.startswith(('"', "'")):
        try:
            value = ast.literal_eval(token)
            return value if isinstance(value, str) else None
        except (ValueError, SyntaxError):
            return None
    return None


def _wrapped_commands(code: str) -> list[tuple[str, str, str | None]]:
    """Recognize tools.<shell tool>({cmd: <literal>, ...}) in recorded JS.

    This is a conservative syntactic scan, not a JS interpreter. Variables,
    concatenation, interpolation and spread properties are not resolved.
    A literal call in a branch or function is only a candidate, not proof it ran.
    """
    tokens = [token for token in _JS_TOKENS.findall(code) if not token.startswith(("//", "/*"))]
    commands = []
    for index in range(len(tokens) - 5):
        if (tokens[index:index + 2] != ["tools", "."] or _name(tokens[index + 2]) not in _SHELL_TOOLS
                or tokens[index + 3:index + 5] != ["(", "{"]):
            continue
        fields: dict[str, str] = {}
        cursor = index + 5
        valid = True
        while cursor < len(tokens) and tokens[cursor] != "}":
            key = _literal(tokens[cursor]) or tokens[cursor]
            if cursor + 2 >= len(tokens) or tokens[cursor + 1] != ":":
                valid = False
                break
            start = cursor + 2
            cursor = start
            depth = 0
            while cursor < len(tokens):
                token = tokens[cursor]
                if depth == 0 and token in {",", "}"}:
                    break
                if token in {"{", "[", "("}:
                    depth += 1
                elif token in {"}", "]", ")"}:
                    depth -= 1
                cursor += 1
            fields.pop(key, None)  # A later dynamic value overrides an earlier literal.
            if cursor == start + 1 and (value := _literal(tokens[start])) is not None:
                fields[key] = value
            if cursor < len(tokens) and tokens[cursor] == ",":
                cursor += 1
        if not valid or tokens[cursor:cursor + 2] != ["}", ")"]:
            continue
        command = next((fields[key] for key in _COMMAND_KEYS if key in fields), None)
        if command is not None:
            commands.append((command, tokens[index + 2], fields.get("workdir") or fields.get("cwd")))
    return commands


def _commands(event: Event) -> list[tuple[str, str, str | None, bool]]:
    arguments = event.arguments
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    tool = _name(event.tool_name)
    if event.action_kind == "shell.exec" or tool in _SHELL_TOOLS:
        cwd = event.cwd
        if isinstance(arguments, dict):
            cwd = arguments.get("workdir") or arguments.get("cwd") or arguments.get("Cwd") or cwd
            value = next((arguments[key] for key in _COMMAND_KEYS if key in arguments), None)
        else:
            value = arguments
        command = _script(value)
        return [(command, event.tool_name or tool, cwd, False)] if command is not None else []
    if tool == "exec":
        code = arguments.get("code") if isinstance(arguments, dict) else arguments
        if isinstance(code, str):
            return [(command, name, cwd or event.cwd, True) for command, name, cwd in _wrapped_commands(code)]
    return []


class ShellCommand(BehavioralPattern):
    """Find shell tool calls whose script starts with or contains literal text.

    At least one filter is required; supplying both means AND. Matching is case
    sensitive unless requested otherwise. Only ``startswith`` ignores leading
    whitespace. The entire script is tested, not each pipeline or line separately.
    This checks submitted command text; it does not prove a mentioned command ran.

    Direct calls include their reported result status (``unknown`` if absent).
    Literal shell calls inside Codex ``functions.exec`` are also surfaced, with
    ``wrapped=True`` and status ``unknown``: wrapper success cannot establish the
    outcome or execution of an individual nested command. Dynamic JS is skipped.

        session.match(ShellCommand(startswith="graphify query"))
        session.match(ShellCommand(contains="scripts/check.sh"))
    """

    name = "shell_command"
    description = "A recorded shell script matches the requested text."

    def __init__(self, *, startswith: str | None = None, contains: str | None = None,
                 case_sensitive: bool = True):
        if startswith is None and contains is None:
            raise ValueError("provide startswith or contains")
        for label, value in (("startswith", startswith), ("contains", contains)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{label} must be a non-blank string")
        self.startswith, self.contains, self.case_sensitive = startswith, contains, case_sensitive

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        results = {(_stream_key(event), event.call_id): (index, event)
                   for index, event in enumerate(events) if event.kind == "tool_result" and event.call_id}
        matches = []
        for index, event in enumerate(events):
            if event.kind != "tool_call":
                continue
            for command_index, (command, tool, cwd, wrapped) in enumerate(_commands(event)):
                text = command if self.case_sensitive else command.casefold()
                prefix = self.startswith if self.case_sensitive else (self.startswith or "").casefold()
                fragment = self.contains if self.case_sensitive else (self.contains or "").casefold()
                if prefix is not None and not text.lstrip().startswith(prefix):
                    continue
                if fragment is not None and fragment not in text:
                    continue
                paired = results.get((_stream_key(event), event.call_id)) if event.call_id else None
                if paired and paired[0] <= index:
                    paired = None
                end, result = paired if paired else (index, None)
                status = result.status if result is not None else None
                matches.append(PatternMatch(
                    pattern_name=self.name, description=f"Shell command matched: {command}",
                    session_id=event.session_id,
                    start_index=index, end_index=end, events=tuple(events[index:end + 1]),
                    context={"command": command, "command_index": command_index,
                             "tool": tool, "cwd": cwd, "wrapped": wrapped,
                             "status": "unknown" if wrapped else status or "unknown",
                             "wrapper_status": status if wrapped else None, "call_id": event.call_id,
                             "result_event_id": result.id if result is not None else None},
                ))
        return matches
