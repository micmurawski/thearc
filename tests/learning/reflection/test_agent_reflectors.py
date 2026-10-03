"""Adapter contracts and optional no-inference runtime probes."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from thearc.learning import (
    AntigravityReflector,
    AntigravityReflectorConfig,
    ClaudeReflector,
    ClaudeReflectorConfig,
    ReflectionError,
)
from thearc.learning.reflection.engine import reflection_schema
from thearc.learning.reflection.providers import antigravity as agy
from thearc.learning.reflection.providers import claude
from thearc.learning.reflection.providers.cli import run_cli

OUTPUT = {"summary": "Test-only output", "items": [], "limitations": ["Synthetic fixture"]}


def claude_stream():
    return [
        {"type": "system", "subtype": "init", "session_id": "thread", "tools": [], "mcp_servers": []},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Inspected evidence."}]}},
        {"type": "result", "subtype": "success", "is_error": False, "session_id": "thread",
         "structured_output": OUTPUT, "usage": {"input_tokens": 1}},
    ]


def encode(events):
    return "\n".join(map(json.dumps, events))


def test_claude_invocation_is_isolated_and_prompt_uses_stdin(monkeypatch):
    calls = []
    monkeypatch.setattr(claude, "probe_claude", lambda config: {"executable": "/fake/claude"})

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[argv.index("--tools") + 1] == ""
        assert "--safe-mode" in argv and "--disable-slash-commands" in argv
        assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
        assert json.loads(argv[argv.index("--settings") + 1])["disableAllHooks"] is True
        assert "--continue" not in argv and "--resume" not in argv
        assert "PRIVATE EVIDENCE" not in " ".join(argv)
        assert kwargs["input_text"] == "PRIVATE EVIDENCE"
        assert not list(kwargs["cwd"].iterdir())
        return encode(claude_stream())

    monkeypatch.setattr(claude, "run_cli", run)
    result = claude._run_claude("PRIVATE EVIDENCE", reflection_schema(), ClaudeReflectorConfig(model="test"))
    assert json.loads(result["final_response"]) == OUTPUT
    assert not calls[0][1]["cwd"].exists()


@pytest.mark.parametrize("change,code", [
    (lambda events: events[0].update(tools=["Bash"]), "isolation"),
    (lambda events: events[0].update(mcp_servers=[{"name": "external"}]), "isolation"),
    (lambda events: events[0].update(skills=["graphify"]), "isolation"),
    (lambda events: events[1].update(type="tool_use"), "isolation"),
    (lambda events: events[-1].update(is_error=True), "incomplete"),
    (lambda events: events[-1].update(subtype="error_max_turns"), "incomplete"),
    (lambda events: events[-1].pop("structured_output"), "invalid_output"),
    (lambda events: events[-1].update(permission_denials=[{"tool": "Bash"}]), "isolation"),
    (lambda events: events.pop(0), "isolation"),
    (lambda events: events.append(events[-1]), "invalid_output"),
])
def test_claude_rejects_bad_or_unsafe_streams(change, code):
    events = claude_stream()
    change(events)
    with pytest.raises(ReflectionError) as exc:
        claude._parse_stream(encode(events))
    assert exc.value.code == code


def test_claude_allows_only_structured_output_tool():
    events = claude_stream()
    events[0]["tools"] = ["StructuredOutput"]
    events.insert(2, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "StructuredOutput", "id": "output", "input": OUTPUT},
    ]}})
    events.insert(3, {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "output", "content": "OK"},
    ]}})
    assert claude._parse_stream(encode(events))["status"] == "completed"


def test_claude_version_and_flags_gate(monkeypatch):
    monkeypatch.setattr(claude.shutil, "which", lambda executable: "/fake/claude")
    monkeypatch.setattr(claude, "run_cli", lambda *args, **kwargs: "different version")
    with pytest.raises(ReflectionError) as exc:
        claude.probe_claude(ClaudeReflectorConfig(model="test"))
    assert exc.value.code == "runtime_version"


@pytest.mark.parametrize("code,timeout,limit,expected", [
    ("import time; time.sleep(10)", 0.1, 1000, "timeout"),
    ("print('x'*10000)", 5, 100, "output_too_large"),
    ("import sys; print('SECRET',file=sys.stderr); sys.exit(1)", 5, 1000, "runtime_error"),
])
def test_cli_transport_errors_are_bounded_and_private(tmp_path, code, timeout, limit, expected):
    with pytest.raises(ReflectionError) as exc:
        run_cli([sys.executable, "-c", code], cwd=tmp_path, timeout=timeout, max_bytes=limit)
    assert exc.value.code == expected
    assert "SECRET" not in str(exc.value)


def test_cli_transport_stdin(tmp_path):
    assert run_cli([sys.executable, "-c", "import sys; print(sys.stdin.read())"], cwd=tmp_path,
                   timeout=5, input_text="evidence") == "evidence\n"


def test_antigravity_real_config_has_only_finish_and_no_shared_state(tmp_path):
    pytest.importorskip("google.antigravity")
    options = agy._sdk_config(AntigravityReflectorConfig(model="explicit-model"), reflection_schema(), tmp_path)
    assert options.capabilities.enabled_tools == ["finish"]
    assert options.capabilities.enable_subagents is False
    assert options.workspaces == [str(tmp_path)]
    assert options.app_data_dir == str(tmp_path / "app-data")
    assert options.save_dir == str(tmp_path / "sessions")
    assert not any([options.skills_paths, options.hooks, options.triggers, options.mcp_servers,
                    options.tools, options.subagents, options.conversation_id])
    assert options.models[0].name == "explicit-model"
    assert options.models[0].endpoint.options.thinking_level == "medium"


@pytest.mark.parametrize("mode,expected", [
    ("success", None), ("tool", "isolation"), ("incomplete", "incomplete"),
    ("invalid", "invalid_output"), ("timeout", "timeout"),
])
def test_antigravity_lifecycle_and_output(monkeypatch, mode, expected):
    sdk = pytest.importorskip("google.antigravity")
    from google.antigravity.types import StopReason, Text, ToolCall

    observed = {}

    class Response:
        stop_reason = StopReason.MAX_MODEL_CALLS_EXCEEDED if mode == "incomplete" else StopReason.UNSPECIFIED
        usage_metadata = None

        @property
        def chunks(self):
            async def stream():
                if mode == "timeout":
                    await asyncio.sleep(10)
                if mode == "tool":
                    yield ToolCall(id="call", name="run_command", arguments={})
                else:
                    yield Text(text="Finished", step_index=1)
            return stream()

        async def structured_output(self):
            return None if mode == "invalid" else OUTPUT

    class Agent:
        conversation_id = "test-only-thread"

        def __init__(self, options):
            observed["options"] = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            observed["closed"] = True

        async def chat(self, prompt):
            observed["prompt"] = prompt
            return Response()

    monkeypatch.setattr(sdk, "Agent", Agent)
    config = AntigravityReflectorConfig(model="test", timeout_seconds=0.05 if mode == "timeout" else 5)
    if expected:
        with pytest.raises(ReflectionError) as exc:
            agy._run_antigravity("inspect now", reflection_schema(), config)
        assert exc.value.code == expected
    else:
        assert json.loads(agy._run_antigravity("inspect now", reflection_schema(), config)["final_response"]) == OUTPUT
    assert observed["prompt"] == "inspect now" and observed["closed"]
    assert not Path(observed["options"].workspaces[0]).exists()


def test_backend_identities_and_config_validation():
    assert ClaudeReflector(ClaudeReflectorConfig(model="test")).backend.agent == "claude"
    assert AntigravityReflector(AntigravityReflectorConfig(model="test")).backend.agent == "antigravity"
    with pytest.raises(ValueError):
        ClaudeReflectorConfig(model="test", reasoning_effort="ultra")
    with pytest.raises(ValueError):
        AntigravityReflectorConfig(model="test", reasoning_effort="max")


@pytest.mark.skipif(os.environ.get("THEARC_TEST_CLAUDE_RUNTIME") != "1", reason="Opt-in no-inference CLI probe")
def test_installed_claude_flags_without_inference():
    assert claude.probe_claude(ClaudeReflectorConfig(model="not-used"))["inference"] is False


@pytest.mark.skipif(os.environ.get("THEARC_TEST_AGY_RUNTIME") != "1", reason="Opt-in no-inference SDK startup probe")
def test_installed_antigravity_startup_without_inference(monkeypatch):
    sdk = pytest.importorskip("google.antigravity")
    from google.antigravity.types import StopReason

    monkeypatch.setenv("GEMINI_API_KEY", "thearc-test-not-a-real-credential")

    original = agy._sdk_config
    called = []

    def options(*args, **kwargs):
        config = original(*args, **kwargs)
        for target in config.models:
            target.endpoint.api_key = "thearc-test-not-a-real-credential"
        return config

    class Response:
        stop_reason = StopReason.UNSPECIFIED
        usage_metadata = None

        @property
        def chunks(self):
            async def empty():
                if False:
                    yield None
            return empty()

        async def structured_output(self):
            return OUTPUT

    async def no_inference(self, prompt):
        called.append(self.is_started)
        return Response()

    monkeypatch.setattr(agy, "_sdk_config", options)
    monkeypatch.setattr(sdk.Agent, "chat", no_inference)
    result = agy._run_antigravity("not sent to any model", reflection_schema(),
                                  AntigravityReflectorConfig(model="not-used", timeout_seconds=20))
    assert result["status"] == "completed" and called == [True]
