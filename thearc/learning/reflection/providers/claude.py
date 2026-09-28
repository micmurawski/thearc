"""Claude Code headless adapter; no CLI invocation during prepare()."""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from thearc.learning.reflection.backend import ReflectionBackend, ReflectionRunner
from thearc.learning.reflection.engine import REFLECTOR_PROMPT, AgentReflector, ReflectionError, ReflectorConfig
from thearc.learning.reflection.providers.cli import run_cli

CLI_VERSION = "2.1.178"
ADAPTER_VERSION = "claude-cli-1"
REQUIRED_FLAGS = ("--safe-mode", "--tools", "--strict-mcp-config", "--mcp-config", "--settings",
                  "--setting-sources", "--disable-slash-commands", "--system-prompt", "--no-session-persistence",
                  "--json-schema", "--output-format", "--effort", "--no-chrome", "--permission-mode")


class ClaudeReflectorConfig(ReflectorConfig):
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    executable: str = "claude"


class ClaudeReflector(AgentReflector):
    def __init__(self, config: ClaudeReflectorConfig, *, artifact_dir: str | Path | None = None,
                 runner: ReflectionRunner | None = None, resume: bool = False):
        super().__init__(config, backend=ReflectionBackend(agent="claude", name="claude-code-cli",
                                                         version=f"{CLI_VERSION}/{ADAPTER_VERSION}"),
                         runner=runner or _run_claude, artifact_dir=artifact_dir, resume=resume)


def _environment() -> dict[str, str]:
    # Preserve login/API credentials and OS settings, not inherited agent behavior switches.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("CLAUDE_CODE_") or key == "CLAUDE_CODE_OAUTH_TOKEN"}
    env.update(CLAUDE_CODE_SAFE_MODE="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def probe_claude(config: ClaudeReflectorConfig) -> dict:
    """Version/flag check only. Does not authenticate, start a turn, or prove live isolation."""
    executable = shutil.which(config.executable)
    if executable is None:
        raise ReflectionError("missing_runtime", "Install the Claude Code CLI and authenticate before execution")
    with tempfile.TemporaryDirectory(prefix="thearc-claude-probe-") as temporary:
        cwd = Path(temporary)
        version = run_cli([executable, "--version"], cwd=cwd, env=_environment(), timeout=10).strip()
        if version != f"{CLI_VERSION} (Claude Code)":
            raise ReflectionError("runtime_version", f"This adapter is pinned to Claude Code {CLI_VERSION}")
        help_text = run_cli([executable, "--help"], cwd=cwd, env=_environment(), timeout=10)
        if any(flag not in help_text for flag in REQUIRED_FLAGS):
            raise ReflectionError("capability", "Claude CLI is missing required isolation/output flags")
    return {"executable": executable, "version": CLI_VERSION, "inference": False}


def _parse_stream(text: str) -> dict:
    initialized = False
    result = None
    session_id = None
    output_calls = set()
    try:
        for line in text.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or result is not None:
                raise ReflectionError("invalid_output", "Unexpected Claude stream envelope")
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                if initialized or "tools" not in event or "mcp_servers" not in event:
                    raise ReflectionError("isolation", "Missing or duplicate Claude capability declaration")
                if (not isinstance(event["tools"], list) or set(event["tools"]) - {"StructuredOutput"}
                        or event["mcp_servers"] or event.get("plugins") or event.get("skills")):
                    raise ReflectionError("isolation", "Claude exposed operational tools or customizations")
                session_id = event.get("session_id")
                initialized = True
            elif not initialized:
                raise ReflectionError("isolation", "Claude did not declare isolated capabilities before its response")
            elif kind == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        if block.get("name") != "StructuredOutput":
                            raise ReflectionError("isolation", "Claude attempted an operational tool")
                        output_calls.add(block["id"])
                    elif block.get("type") not in {"text", "thinking", "redacted_thinking"}:
                        raise ReflectionError("isolation", "Unexpected Claude response block")
            elif kind == "user":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") != "tool_result" or block.get("tool_use_id") not in output_calls:
                        raise ReflectionError("isolation", "Unexpected Claude tool result")
            elif kind == "result":
                result = event
            else:
                raise ReflectionError("isolation", "Unexpected Claude lifecycle or tool event")
        if (not result or result.get("subtype") != "success" or result.get("is_error") is not False
                or not session_id or result.get("session_id") != session_id):
            raise ReflectionError("incomplete", "Claude did not complete the reflection turn")
        if result.get("permission_denials"):
            raise ReflectionError("isolation", "Claude attempted a permission-gated operation")
        output = result.get("structured_output")
        if not isinstance(output, dict):
            raise ReflectionError("invalid_output", "Claude did not return structured reflection output")
        return {"status": "completed", "final_response": json.dumps(output),
                "thread_id": session_id, "turn_id": result.get("uuid"),
                "usage": result.get("usage"), "sdk_version": None, "runtime_version": CLI_VERSION}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ReflectionError("invalid_output", "Malformed Claude stream") from exc


def _run_claude(prompt: str, schema: dict, config: ClaudeReflectorConfig, *, instructions=REFLECTOR_PROMPT) -> dict:
    runtime = probe_claude(config)
    with tempfile.TemporaryDirectory(prefix="thearc-claude-reflector-") as temporary:
        command = [runtime["executable"], "--print", "--safe-mode", "--tools", "",
                   "--disable-slash-commands", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--setting-sources", "", "--settings", '{"disableAllHooks":true,"autoMemoryEnabled":false}',
                   "--no-chrome", "--no-session-persistence", "--permission-mode", "dontAsk",
                   "--system-prompt", instructions, "--model", config.model, "--effort", config.reasoning_effort,
                   "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(schema)]
        return _parse_stream(run_cli(command, cwd=Path(temporary), env=_environment(),
                                     timeout=config.timeout_seconds, input_text=prompt))
