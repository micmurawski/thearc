"""Harness runtimes: start or fork a reflector session and run its turn.

A runtime opens a **conversation** in a working directory that holds the evaluated
agent's files, with the ``submit_reflection`` MCP tool connected for that run only;
``send`` delivers one message and returns the reflector's final answer for that turn.
Reflectors may only read and submit: each runtime restricts the harness to its read-only
tools plus the submit tool, and turns off the user's own customizations (hooks, skills,
context files) where the harness allows it.

| Harness | How | Submit tool | Handoff (fork) |
| --- | --- | --- | --- |
| codex | ``openai-codex`` SDK, read-only sandbox | config override | yes |
| claude | ``claude -p``, Read/Grep/Glob only | ``--mcp-config`` | yes (``--resume … --fork-session``) |
| pi | ``pi -p --mode json``, read/grep/find/ls only | ``-e`` extension | yes (``--fork``) |
| antigravity | — | no per-run MCP servers | no |
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thearc.sessions import InstalledSession


class ReflectionError(RuntimeError):
    """A reflection could not be completed; ``code`` classifies why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


SERVER = "thearc"
TOOL = "submit_reflection"


@dataclass(frozen=True)
class ToolEndpoint:
    """Where a runtime finds thearc's tools for one run: an MCP server over streamable HTTP on localhost."""

    url: str
    tools: tuple[str, ...] = (TOOL,)

    @property
    def qualified(self) -> tuple[str, ...]:
        """The tools' names as Claude and Pi expose them."""
        return tuple(f"mcp__{SERVER}__{tool}" for tool in self.tools)


class Conversation(ABC):
    """One reflector session. ``id`` is the harness's own session (or thread / conversation) ID."""

    id: str | None = None

    @abstractmethod
    def send(self, message: str) -> str:
        """Deliver ``message`` and return the reflector's final answer for that turn."""


class Runtime(ABC):
    """Runs reflector sessions in one harness."""

    harness: str
    can_fork: bool = True

    def __init__(self, *, model: str | None = None, effort: str | None = None, timeout: float = 900):
        self.model, self.effort, self.timeout = model, effort, timeout

    @abstractmethod
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> AbstractContextManager[Conversation]:
        """Open a session in ``workspace`` with ``tool`` connected; with ``fork``, a fork of an installed session.

        Sessions only read files, unless ``edit``: then they may also edit files inside ``workspace``
        (never run commands — anything else goes through thearc's tools).
        """


# Set by a harness inside its own sessions. A runtime started from one (e.g. by /evolve) must not inherit them, or the
# harness takes it for a nested session of the user's.
NESTING = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")


def _run(command: list[str], *, cwd: Path, timeout: float, stdin: str | None = None) -> str:
    executable = shutil.which(command[0])
    if executable is None:
        raise ReflectionError("missing_runtime", f"{command[0]} is not installed or not on PATH")
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            cwd=cwd,
            env={name: value for name, value in os.environ.items() if name not in NESTING},
            input=stdin or "",  # Always close stdin: Pi otherwise waits for piped input on ours.
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReflectionError("timeout", f"{command[0]} did not finish within {timeout:.0f}s") from exc
    if result.returncode != 0 and not result.stdout.strip():
        raise ReflectionError("runtime_failed", f"{command[0]} failed: {result.stderr.strip()[-500:]}")
    return result.stdout


# --- Codex -------------------------------------------------------------------------------------------------------

# Configuration overrides for the reflector's own run only; no user configuration file is edited.
_CODEX_OVERRIDES = (
    "features.hooks=false",
    "features.plugins=false",
    "features.apps=false",
    "features.memories=false",
    "features.multi_agent=false",
    "features.skip_host_skill_discovery=true",
    "skills.include_instructions=false",
    "skills.bundled.enabled=false",
    "project_doc_max_bytes=0",
    'web_search="disabled"',
    "notify=[]",
)


class CodexRuntime(Runtime):
    harness = "codex"

    @contextmanager
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> Iterator[Conversation]:
        try:
            from openai_codex import CodexConfig, Thread
            from openai_codex.client import CodexClient
            from openai_codex.generated.v2_all import ThreadTurnsListResponse
        except ImportError as exc:
            raise ReflectionError("missing_runtime", "Install thearc[codex] to reflect with Codex") from exc
        overrides = (
            *_CODEX_OVERRIDES,
            f'mcp_servers.{SERVER}.url="{tool.url}"',
            f'mcp_servers.{SERVER}.default_tools_approval_mode="approve"',
        )
        client = CodexClient(
            CodexConfig(cwd=str(workspace), client_name="thearc_reflector", config_overrides=overrides)
        )
        client.start()
        try:
            client.initialize()
            params: dict[str, Any] = {
                "cwd": str(workspace),
                "sandbox": "workspace-write" if edit else "read-only",
                "approvalPolicy": "never",
                "ephemeral": False,
                **({"model": self.model} if self.model else {}),
            }
            started: Any
            if fork is None:
                started = client.thread_start(params)
            else:
                turns = client.request(
                    "thread/turns/list",
                    {"threadId": fork.session_id, "sortDirection": "desc", "limit": 1, "itemsView": "notLoaded"},
                    response_model=ThreadTurnsListResponse,
                ).data
                if not turns:
                    raise ReflectionError("missing_history", "The handed-off Codex session has no turns")
                started = client.thread_fork(
                    fork.session_id, {**params, "lastTurnId": turns[0].id, "excludeTurns": True}
                )
            yield _CodexConversation(Thread(client, started.thread.id), self.effort)
        finally:
            client.close()


class _CodexConversation(Conversation):
    def __init__(self, thread: Any, effort: str | None):
        self.thread, self.effort, self.id = thread, effort, thread.id

    def send(self, message: str) -> str:
        result = self.thread.run(message, **({"effort": self.effort} if self.effort else {}))
        if result.status.value != "completed" or not result.final_response:
            raise ReflectionError("incomplete", f"Codex turn ended with status {result.status.value}")
        return str(result.final_response)


# --- Claude Code -------------------------------------------------------------------------------------------------


class ClaudeRuntime(Runtime):
    harness = "claude"

    @contextmanager
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> Iterator[Conversation]:
        if fork is not None and not Path(fork.path).is_file():
            raise ReflectionError("missing_history", f"Installed Claude session not found: {fork.path}")
        conversation = _ClaudeConversation(self, workspace, tool, fork, edit)
        try:
            yield conversation
        finally:
            if fork is not None and conversation.id is not None:
                # Claude records the fork beside the copy, in the store of the folder the session ran in, where it
                # would count as a session of that project.
                transcript = Path(fork.path).with_name(conversation.id)
                transcript.with_suffix(".jsonl").unlink(missing_ok=True)
                shutil.rmtree(transcript, ignore_errors=True)  # Its tool results and subagents, if any.


class _ClaudeConversation(Conversation):
    def __init__(
        self, runtime: ClaudeRuntime, workspace: Path, tool: ToolEndpoint, fork: InstalledSession | None, edit: bool
    ):
        self.runtime, self.workspace, self.tool, self.fork, self.edit = runtime, workspace, tool, fork, edit
        # Claude finds a session by the directory it was recorded in, so a fork runs there.
        self.cwd = Path(_claude_cwd(fork)) if fork is not None else workspace

    def send(self, message: str) -> str:
        servers = {"mcpServers": {SERVER: {"type": "http", "url": self.tool.url}}}
        command = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--tools",
            "Read,Grep,Glob,Edit,Write" if self.edit else "Read,Grep,Glob",
            "--allowedTools",
            ",".join([*self.tool.qualified, *(["Edit", "Write"] if self.edit else [])]),
            "--permission-mode",
            "dontAsk",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(servers),
            "--disable-slash-commands",
            "--settings",
            '{"disableAllHooks":true}',
            "--add-dir",
            str(self.workspace),
        ]
        command += ["--model", self.runtime.model] if self.runtime.model else []
        command += ["--effort", self.runtime.effort] if self.runtime.effort else []
        if self.id is not None:
            command += ["--resume", self.id]
        elif self.fork is not None:
            command += ["--resume", self.fork.session_id, "--fork-session"]
        if not self.cwd.is_dir():
            raise ReflectionError("missing_workspace", f"The session's working directory does not exist: {self.cwd}")
        output = _run(command, cwd=self.cwd, timeout=self.runtime.timeout, stdin=message)
        try:
            result = json.loads(output)
        except ValueError as exc:
            raise ReflectionError("invalid_output", "Claude did not print a JSON result") from exc
        if result.get("is_error") or result.get("subtype") != "success":
            raise ReflectionError("incomplete", f"Claude turn failed: {str(result.get('result'))[:300]}")
        self.id = result.get("session_id") or self.id
        return str(result.get("result") or "")


def _claude_cwd(installed: InstalledSession) -> str:
    """The working directory recorded in an installed Claude transcript."""
    with Path(installed.path).open(encoding="utf-8") as stream:
        for line in stream:
            cwd = json.loads(line).get("cwd")
            if cwd:
                return str(cwd)
    raise ReflectionError("missing_workspace", "The installed Claude session records no working directory")


# --- Pi ----------------------------------------------------------------------------------------------------------


class PiRuntime(Runtime):
    harness = "pi"

    @contextmanager
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> Iterator[Conversation]:
        with tempfile.TemporaryDirectory(prefix="thearc-pi-") as directory:
            extension = Path(directory) / "submit.ts"  # Registers the submit tool's server for this run only.
            config = json.dumps({"url": tool.url, "exposure": "direct"})
            extension.write_text(
                f"export default function (pi: any) {{ pi.registerMcpServer({json.dumps(SERVER)}, {config}); }}\n",
                encoding="utf-8",
            )
            yield _PiConversation(self, workspace, tool, extension, fork, Path(directory), edit)


class _PiConversation(Conversation):
    def __init__(
        self,
        runtime: PiRuntime,
        workspace: Path,
        tool: ToolEndpoint,
        extension: Path,
        fork: InstalledSession | None,
        prompts: Path,
        edit: bool = False,
    ):
        self.runtime, self.workspace, self.tool, self.extension = runtime, workspace, tool, extension
        self.fork, self.prompts, self.turns, self.edit = fork, prompts, 0, edit

    def send(self, message: str) -> str:
        self.turns += 1
        prompt = self.prompts / f"turn-{self.turns}.md"  # Long prompts go in a file Pi includes with @path.
        prompt.write_text(message, encoding="utf-8")
        command = [
            "pi",
            "-p",
            "--mode",
            "json",
            "--tools",
            ",".join(["read", "grep", "find", "ls", *(["edit", "write"] if self.edit else []), *self.tool.qualified]),
            "--no-extensions",
            "-e",
            "builtin:mcp",
            "-e",
            str(self.extension),
            "--no-skills",
            "--no-context-files",
            "--no-prompt-templates",
            "--no-themes",
        ]
        command += ["--model", self.runtime.model] if self.runtime.model else []
        command += ["--thinking", self.runtime.effort] if self.runtime.effort else []
        if self.id is not None:
            command += ["--session", self.id]
        elif self.fork is not None:
            command += ["--fork", str(self.fork.path)]
        else:
            command += ["--session-id", str(uuid.uuid4())]
        output = _run([*command, f"@{prompt}"], cwd=self.workspace, timeout=self.runtime.timeout)
        answer = None
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # Pi prints notices (e.g. a newly created session) as plain text.
            if event.get("type") == "session" and self.id is None:
                self.id = event.get("id")
            message_ = event.get("message") if event.get("type") == "message_end" else None
            if isinstance(message_, dict) and message_.get("role") == "assistant":
                answer = message_
        if answer is None:
            raise ReflectionError("incomplete", "Pi produced no assistant message")
        if answer.get("stopReason") in {"error", "aborted"}:
            raise ReflectionError("incomplete", f"Pi turn ended with {answer.get('stopReason')}")
        return "\n".join(
            block.get("text", "")
            for block in answer.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )


# --- Antigravity -------------------------------------------------------------------------------------------------


class AntigravityRuntime(Runtime):
    """Not available: Antigravity reads MCP servers only from the user's own ``mcp_config.json``."""

    harness = "antigravity"
    can_fork = False

    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> AbstractContextManager[Conversation]:
        raise ReflectionError("capability", "Antigravity cannot connect the submit_reflection tool for one run")


RUNTIMES: dict[str, type[Runtime]] = {
    "codex": CodexRuntime,
    "claude": ClaudeRuntime,
    "pi": PiRuntime,
    "antigravity": AntigravityRuntime,
}
