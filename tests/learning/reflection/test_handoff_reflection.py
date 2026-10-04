"""Native fork boundaries and persisted-history validation."""

from types import SimpleNamespace

import pytest

from thearc.learning.reflection.providers import codex
from thearc.learning.reflection.runtime import ExecutionConfig, NativeSessionRef, ReflectionError

SOURCE = NativeSessionRef(runtime="codex", session_id="original")
CONFIG = ExecutionConfig(model="test")


@pytest.mark.parametrize(
    "mode,expected_error",
    [
        ("success", None),
        ("same_id", "invalid_fork"),
        ("wrong_parent", "invalid_fork"),
        ("ephemeral", "invalid_fork"),
        ("missing_history", "missing_history"),
        ("active", "source_active"),
        ("active_turn", "source_active"),
        ("wrong_boundary", "invalid_fork"),
        ("not_persisted", "incomplete"),
        ("fork_error", "sdk_error"),
        ("tool", "isolation"),
    ],
)
def test_codex_native_fork_boundary_without_inference(tmp_path, monkeypatch, mode, expected_error):
    sdk = pytest.importorskip("openai_codex")
    import openai_codex.client

    calls = []
    source_bytes = b"original native history\n"
    original = tmp_path / "original.jsonl"
    original.write_bytes(source_bytes)
    source = SimpleNamespace(
        id=SOURCE.session_id,
        path=str(original),
        ephemeral=False,
        status=SimpleNamespace(root=SimpleNamespace(type="active" if mode == "active" else "idle")),
        model_dump=lambda **kw: {"cwd": str(tmp_path)},
    )
    child = SimpleNamespace(
        id=SOURCE.session_id if mode == "same_id" else "native-fork",
        forked_from_id="wrong" if mode == "wrong_parent" else SOURCE.session_id,
        ephemeral=mode == "ephemeral",
        path=str(tmp_path / "child.jsonl"),
    )
    completed = False

    class Client:
        def __init__(self, config, approval_handler):
            assert "features.shell_tool=false" in config.config_overrides

        def start(self):
            calls.append("start")

        def initialize(self):
            pass

        def thread_start(self, *args):
            pytest.fail("Handoff must not start an empty thread")

        def thread_resume(self, *args):
            pytest.fail("Handoff must not append to S1")

        def thread_read(self, thread_id):
            return SimpleNamespace(thread=source if thread_id == SOURCE.session_id else child)

        def request(self, method, params, *, response_model):
            if method == "thread/turns/list":
                turn = "source-turn"
                if params["threadId"] == child.id:
                    if mode == "wrong_boundary":
                        turn = "wrong"
                    if completed and mode != "not_persisted":
                        turn = "reflection-turn"
                data = (
                    []
                    if mode == "missing_history"
                    else [
                        SimpleNamespace(
                            id=turn,
                            status=SimpleNamespace(value="inProgress" if mode == "active_turn" else "completed"),
                        )
                    ]
                )
                return SimpleNamespace(data=data)
            if method == "mcpServerStatus/list":
                return SimpleNamespace(data=[], next_cursor=None)
            assert method == "config/read"
            return SimpleNamespace(
                config=SimpleNamespace(
                    model_dump=lambda **kw: {
                        "features": dict.fromkeys(codex._DISABLED_FEATURES, False),
                        "mcp_servers": {"external": {}},
                        "project_doc_max_bytes": 0,
                        "web_search": "disabled",
                        "skills": {"include_instructions": False},
                    }
                )
            )

        def thread_fork(self, thread_id, params):
            calls.append("fork")
            assert thread_id == SOURCE.session_id
            assert params["lastTurnId"] == "source-turn" and params["ephemeral"] is False
            assert params["model"] == CONFIG.model
            assert params["config"]["mcp_servers"]["external"]["enabled"] is False
            assert "baseInstructions" not in params and "developerInstructions" not in params
            if mode == "fork_error":
                raise RuntimeError("fixture")
            return SimpleNamespace(
                thread=child, instruction_sources=[], sandbox=SimpleNamespace(root=SimpleNamespace(type="readOnly"))
            )

        def close(self):
            calls.append("close")

    def run(self, prompt, *, output_schema, effort):
        nonlocal completed
        assert self.id == child.id != SOURCE.session_id
        assert calls[-1] == "receipt"
        assert prompt == "RP only" and output_schema is None
        calls.append("RP")
        completed = True
        return SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            final_response="Reflection",
            id="reflection-turn",
            usage=None,
            items=[SimpleNamespace(type="commandExecution")] if mode == "tool" else [],
        )

    monkeypatch.setattr(openai_codex.client, "CodexClient", Client)
    monkeypatch.setattr(sdk.Thread, "run", run)
    if expected_error:
        with pytest.raises(ReflectionError) as exc:
            codex._run_isolated_sdk(
                "RP only",
                None,
                CONFIG,
                instructions=None,
                fork_from=SOURCE,
                on_fork=lambda receipt: calls.append("receipt"),
            )
        assert exc.value.code == expected_error
        if mode not in {"not_persisted", "tool"}:
            assert "RP" not in calls
    else:
        result = codex._run_isolated_sdk(
            "RP only",
            None,
            CONFIG,
            instructions=None,
            fork_from=SOURCE,
            on_fork=lambda receipt: calls.append("receipt"),
        )
        assert result["thread_id"] == child.id
        assert calls == ["start", "fork", "receipt", "RP", "close"]
    assert calls[-1] == "close"
    assert original.read_bytes() == source_bytes


def test_select_source_turn_searches_pages_and_rejects_unknown_boundary():
    pytest.importorskip("openai_codex")
    calls = []

    class Client:
        def request(self, method, params, **kwargs):
            calls.append(params)
            if params["limit"] == 1:
                return SimpleNamespace(data=[SimpleNamespace(id="latest", status=SimpleNamespace(value="completed"))])
            cursor = params["cursor"]
            return SimpleNamespace(
                data=[SimpleNamespace(id="older" if cursor else "latest", status=SimpleNamespace(value="completed"))],
                next_cursor=None if cursor else "page2",
            )

    assert codex._source_turn(Client(), "source", "older") == "older"
    assert calls[-1]["cursor"] == "page2"
    with pytest.raises(ReflectionError, match="does not belong"):
        codex._source_turn(Client(), "source", "unknown")
