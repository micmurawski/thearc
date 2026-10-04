"""Codex adapter isolation and SDK boundary contracts."""

import inspect
import os
from threading import Event as ThreadEvent
from types import SimpleNamespace

import pytest

from thearc.learning.reflection.providers.codex import (
    _DISABLED_FEATURES,
    SDK_VERSION,
    CodexRuntimeConfig,
    _run_isolated_sdk,
)
from thearc.learning.reflection.runtime import INSTRUCTIONS, ReflectionError


def test_pinned_sdk_contract_without_running_a_model():
    sdk = pytest.importorskip("openai_codex")
    assert sdk.__version__ == SDK_VERSION
    assert "output_schema" in inspect.signature(sdk.Thread.run).parameters
    assert "config_overrides" in inspect.signature(sdk.CodexConfig).parameters


@pytest.mark.parametrize("mode", ["success", "disabled_mcp", "active_mcp", "features", "tool", "timeout"])
def test_real_sdk_boundary_uses_prompt_schema_and_closes(monkeypatch, mode):
    sdk = pytest.importorskip("openai_codex")
    import openai_codex.client

    calls = []
    closed = ThreadEvent()

    class Client:
        def __init__(self, config, approval_handler=None):
            assert callable(approval_handler)
            assert config.cwd.startswith("/")
            assert "features.hooks=false" in config.config_overrides
            assert "project_doc_max_bytes=0" in config.config_overrides

        def start(self):
            calls.append("start")

        def initialize(self):
            calls.append("initialize")

        def request(self, method, params, *, response_model):
            if method == "mcpServerStatus/list":
                if mode in {"disabled_mcp", "active_mcp"}:
                    return SimpleNamespace(
                        data=[
                            SimpleNamespace(
                                runtime_status=SimpleNamespace(
                                    value="disabled" if mode == "disabled_mcp" else "connected"
                                ),
                                tools={},
                                resources=[],
                                resource_templates=[],
                            )
                        ],
                        next_cursor=None,
                    )
                return SimpleNamespace(data=[], next_cursor=None)
            assert method == "config/read"
            return SimpleNamespace(
                config=SimpleNamespace(
                    model_dump=lambda **k: {
                        "features": {**dict.fromkeys(_DISABLED_FEATURES, False), "hooks": mode == "features"},
                        "mcp_servers": {"external": {}},
                        "project_doc_max_bytes": 0,
                        "web_search": "disabled",
                        "skills": {"include_instructions": False},
                    }
                )
            )

        def thread_start(self, params):
            assert params["ephemeral"] is True
            assert params["sandbox"] == "read-only"
            assert params["config"]["mcp_servers"]["external"]["enabled"] is False
            calls.append("thread")
            return SimpleNamespace(
                thread=SimpleNamespace(id="new-reflector-thread"),
                instruction_sources=[],
                sandbox=SimpleNamespace(root=SimpleNamespace(type="readOnly")),
            )

        def close(self):
            calls.append("close")
            closed.set()

    def run(self, prompt, *, output_schema, effort):
        assert prompt == "inspect now" and output_schema == {"type": "object"}
        assert effort == "medium"
        calls.append("prompt-and-schema")
        if mode == "timeout":
            assert closed.wait(2), "Deadline did not close the runtime"
            raise RuntimeError("Runtime closed")
        return SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            final_response="{}",
            id="turn",
            items=[SimpleNamespace(type="commandExecution")] if mode == "tool" else [],
            usage=None,
        )

    monkeypatch.setattr(openai_codex.client, "CodexClient", Client)
    monkeypatch.setattr(sdk.Thread, "run", run)
    config = CodexRuntimeConfig(model="test", timeout_seconds=0.1 if mode == "timeout" else 180)
    if mode not in {"success", "disabled_mcp"}:
        with pytest.raises(ReflectionError) as exc:
            _run_isolated_sdk("inspect now", {"type": "object"}, config, instructions=INSTRUCTIONS)
        assert exc.value.code == ("timeout" if mode == "timeout" else "isolation")
        assert calls[-1] == "close"
        if mode in {"features", "active_mcp"}:
            assert "prompt-and-schema" not in calls
        return
    result = _run_isolated_sdk("inspect now", {"type": "object"}, config, instructions=INSTRUCTIONS)
    assert result["thread_id"] == "new-reflector-thread"
    assert calls == ["start", "initialize", "thread", "prompt-and-schema", "close"]


@pytest.mark.skipif(os.environ.get("THEARC_TEST_CODEX_RUNTIME") != "1", reason="Opt-in local runtime probe")
def test_installed_runtime_isolation_without_inference(monkeypatch):
    """Start a real SDK/runtime thread, but replace the model turn entirely."""
    sdk = pytest.importorskip("openai_codex")
    called = []

    def no_inference(self, prompt, **kwargs):
        called.append(self.id)
        return SimpleNamespace(
            status=SimpleNamespace(value="completed"), final_response="{}", id="no-inference", items=[], usage=None
        )

    monkeypatch.setattr(sdk.Thread, "run", no_inference)
    result = _run_isolated_sdk(
        "unused",
        {"type": "object"},
        CodexRuntimeConfig(model="test-model", timeout_seconds=20),
        instructions=INSTRUCTIONS,
    )
    assert result["turn_id"] == "no-inference" and called
