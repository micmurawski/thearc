"""Find recorded skill access, without consulting today's filesystem or executing code."""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from pathlib import Path
from typing import Any

from thearcv2.resources.agent import MetaAgent
from thearcv2.resources.harness import HARNESSES
from thearcv2.sessions.models import Event

from .patterns import BehavioralPattern, PatternMatch, _stream_key
from .shell import _commands, _name

_ROOTS = {layout.location("skills") for layout in HARNESSES.values()} | {
    ".agents/skills", ".opencode/skills", ".pi/agent/skills",
}
_READ_TOOLS = {"read", "read_file", "view_file"}
_INVOKE_TOOLS = {"skill", "use_skill", "load_skill"}
_INTERPRETERS = {"bash", "sh", "zsh", "python", "python3", "node", "ruby", "perl"}


def _normalize(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/"))


def _absolute(path: str) -> bool:
    return path.startswith("/") or bool(re.match(r"^[A-Za-z]:/", path))


def _resolve(path: str, cwd: str | None) -> str:
    path = _normalize(path)
    return _normalize(posixpath.join(cwd, path)) if cwd and not _absolute(path) else path


def _address(path: str) -> tuple[str, str] | None:
    """A name and skill-relative file, only for canonical or recognized native layouts."""
    path = _normalize(path)
    tail = path[len("skills/"):] if path.startswith("skills/") else None
    if tail is None:
        for root in sorted(_ROOTS, key=len, reverse=True):
            if path.startswith(root + "/"):
                tail = path[len(root) + 1:]
                break
            marker = "/" + root + "/"
            if marker in path:
                tail = path.rsplit(marker, 1)[1]
                break
    if tail is None:
        return None
    name, _, relative = tail.partition("/")
    return (name, relative) if name else None


def _arguments(event: Event) -> dict[str, Any]:
    value = event.arguments
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _paths(command: str, cwd: str | None) -> list[tuple[str, str, str | None]]:
    """Conservative literal paths in common readers and script invocations.

    Shell syntax is never evaluated. Dynamic paths, substitutions, heredocs,
    and shell programs with unrecognized argument semantics are skipped.
    """
    if any(token in command for token in ("<<", "$(", "`")):
        return []
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in ";&|\n" for char in token):
            segments.append([])
        else:
            segments[-1].append(token)
    paths = []
    for parts in segments:
        if not parts or any(token in {"(", ")"} for token in parts):
            continue
        executable = parts[0].rsplit("/", 1)[-1]
        if executable == "cd" and len(parts) == 2 and not parts[1].startswith("-") and "$" not in parts[1]:
            cwd = _resolve(parts[1], cwd)
            continue
        if executable in {"cat", "head", "tail"}:
            skip = False
            for argument in parts[1:]:
                if skip:
                    skip = False
                    continue
                if argument in {"-n", "-c", "--lines", "--bytes"} and executable != "cat":
                    skip = True
                    continue
                if argument in {">", ">>", "<", "2>", "2>>"}:
                    break
                if not argument.startswith("-") and "$" not in argument:
                    paths.append(("read", argument, cwd))
        elif executable == "sed":
            arguments = parts[1:]
            if arguments and arguments[0] == "-n":
                arguments = arguments[1:]
            # Only simple print expressions; -i and arbitrary sed programs are not reads.
            if arguments and re.fullmatch(r"\d*(?:,\d+)?p", arguments[0]):
                for argument in arguments[1:]:
                    if argument in {">", ">>", "<"}:
                        break
                    if not argument.startswith("-") and "$" not in argument:
                        paths.append(("read", argument, cwd))
        elif executable in _INTERPRETERS:
            # Do not interpret -c/-e programs, modules, or interpreter options.
            if len(parts) > 1 and not parts[1].startswith("-") and "$" not in parts[1]:
                paths.append(("executed", parts[1], cwd))
        elif "/" in parts[0] and "$" not in parts[0]:
            paths.append(("executed", parts[0], cwd))
    return paths


class SkillUsage(BehavioralPattern):
    """Evidence of skill requests, invocations, reads, and script execution attempts.

    ``agent`` selects its defined skills/files. ``path='skills/name[/file]'``
    selects a portable skill or subtree across known harness locations; other
    paths select a specific historical location. With both, path narrows agent.
    No file must exist today. Relative paths use the recorded cwd/workdir; ~ and
    environment variables are never expanded using the current machine.

    Paths identify candidate skills, not verified versions. The agent's revision
    labels the supplied configuration only. Shell findings describe static paths
    in submitted scripts, not proof that a branch executed or instructions were
    fully loaded. Unknown/dynamic syntax is skipped. Behaviorally following a
    skill, automatic prompt injection, and associated CLI names are not inferred.
    """

    name = "skill_usage"
    description = "Recorded evidence of access to a selected skill."

    def __init__(self, *, agent: MetaAgent | None = None, path: str | Path | None = None):
        if agent is None and path is None:
            raise ValueError("provide agent or path")
        if agent is not None and not isinstance(agent, MetaAgent):
            raise TypeError("agent must be a MetaAgent")
        if path is not None and (not isinstance(path, (str, Path)) or not str(path).strip()):
            raise ValueError("path must be a non-blank path")
        self.path = _normalize(str(path)) if path is not None else None
        self.portable = bool(self.path and self.path.startswith("skills/"))
        address = _address(self.path) if self.path else None
        if self.portable and address is None:
            raise ValueError("portable paths must select skills/<name>")
        self.selected_name = address[0] if address else None
        if self.path and not address:
            # An explicit custom directory or SKILL.md also identifies a single skill.
            directory = posixpath.dirname(self.path) if self.path.endswith("/SKILL.md") else self.path
            self.selected_name = posixpath.basename(directory)
            if self.selected_name in {"", ".", ".."} or self.path == "skills":
                raise ValueError("path must select a skill directory or file")
        self.files = ({name: {"SKILL.md", *skill.files} for name, skill in agent.skills.items()}
                      if agent is not None else None)
        if self.files is not None and self.selected_name is not None and self.selected_name not in self.files:
            raise ValueError(f"agent has no skill {self.selected_name!r}")
        self.agent_revision = agent.revision if agent is not None else None

    def _select(self, observed: str, cwd: str | None) -> dict[str, Any] | None:
        if not observed or "$" in observed:
            return None
        resolved = _resolve(observed, cwd)
        address = _address(resolved) or _address(observed)
        basis = "portable_path"
        if self.path and not self.portable:
            expected = _resolve(self.path, cwd)
            if resolved != expected and not resolved.startswith(expected.rstrip("/") + "/"):
                return None
            basis = "exact_path"
            if address is None:
                relative = ("SKILL.md" if self.path.endswith("/SKILL.md") else
                            resolved[len(expected):].lstrip("/"))
                address = (self.selected_name or "", relative)
        if address is None:
            return None
        name, relative = address
        if not relative or (self.selected_name is not None and name != self.selected_name):
            return None
        canonical = f"skills/{name}/{relative}"
        if self.portable and canonical != self.path and not canonical.startswith(str(self.path).rstrip("/") + "/"):
            return None
        if self.files is not None and (name not in self.files or relative not in self.files[name]):
            return None
        return {"skill": name, "canonical_path": canonical, "observed_path": observed,
                "resolved_path": resolved, "basis": basis}

    def _by_name(self, name: str) -> dict[str, Any] | None:
        # A name-only invocation cannot establish an explicit installation path.
        if not name or name in {".", ".."} or "/" in name or "\\" in name or (self.path and not self.portable):
            return None
        selected = self._select(f"skills/{name}/SKILL.md", None)
        if selected is not None:
            selected.update(observed_path=None, resolved_path=None, basis="skill_name")
        return selected

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        results = {(_stream_key(event), event.call_id): (index, event)
                   for index, event in enumerate(events) if event.kind == "tool_result" and event.call_id}
        matches = []
        for index, event in enumerate(events):
            observations: list[dict[str, Any]] = []
            if event.kind == "message" and event.role == "user":
                request = re.match(r"^\s*/([\w.-]+)(?=\s|$)", event.text)
                selected = self._by_name(request[1]) if request else None
                if selected is not None:
                    observations.append({**selected, "usage": "requested"})
            if event.kind == "tool_call":
                args = _arguments(event)
                tool = _name(event.tool_name)
                cwd = args.get("workdir") or args.get("cwd") or args.get("Cwd") or event.cwd
                if tool in _INVOKE_TOOLS:
                    name = args.get("skill") or args.get("name")
                    selected = self._by_name(name) if isinstance(name, str) else None
                    if selected is not None:
                        observations.append({**selected, "usage": "invoked"})
                if event.action_kind == "file.read" or tool in _READ_TOOLS:
                    path = next((args[key] for key in ("file_path", "path", "filePath", "TargetFile", "target_file")
                                 if isinstance(args.get(key), str)), None)
                    selected = self._select(path, cwd) if path is not None else None
                    if selected is not None:
                        observations.append({**selected, "usage": "read"})
                for command_index, (command, _, cwd, wrapped) in enumerate(_commands(event)):
                    for usage, path, effective_cwd in _paths(command, cwd):
                        selected = self._select(path, effective_cwd)
                        if selected is not None:
                            observations.append({**selected, "usage": usage, "command": command,
                                                 "command_index": command_index, "wrapped": wrapped})
            paired = results.get((_stream_key(event), event.call_id)) if event.call_id else None
            if paired and (event.kind != "tool_call" or paired[0] <= index):
                paired = None
            end, result = paired if paired else (index, None)
            seen: list[dict[str, Any]] = []
            for observation in observations:
                if observation in seen:
                    continue
                seen.append(observation)
                wrapped = observation.get("wrapped", False)
                status = result.status if result is not None else None
                context = {**observation, "status": "unknown" if wrapped else status or "unknown",
                           "outcome_scope": "request" if observation["usage"] == "requested" else
                           "wrapper" if wrapped else
                           "shell_script" if "command" in observation else "tool_call",
                           "wrapper_status": status if wrapped else None,
                           "agent_revision": self.agent_revision, "tool": event.tool_name,
                           "call_id": event.call_id, "result_event_id": result.id if result is not None else None}
                matches.append(PatternMatch(
                    pattern_name=self.name,
                    description=f"Skill {observation['skill']!r}: {observation['usage']} evidence.",
                    session_id=event.session_id, start_index=index, end_index=end,
                    events=tuple(events[index:end + 1]), context=context,
                ))
        return matches
