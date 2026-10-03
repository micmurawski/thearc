"""Native reflection inherits S1, appends only RP, and persists a distinct S1R."""

import copy
import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from thearc.cli import main
from thearc.learning.reflection import (
    ForkReceipt,
    HandoffReflector,
    HandoffReflectorConfig,
    NativeSessionRef,
    ReflectionBackend,
    ReflectionError,
    create_handoff_reflector,
)
from thearc.learning.reflection.providers import codex
from thearc.learning.sessions.models import Session

SOURCE = NativeSessionRef(runtime="codex", session_id="native-source")
CONFIG = HandoffReflectorConfig(model="reflector-model")


def reflector(runner, tmp_path):
    return HandoffReflector(CONFIG, backend=ReflectionBackend(agent="codex", name="test-native", version="1"),
                            runner=runner, artifact_dir=tmp_path)


def test_preview_has_only_prompt_and_locator_without_calling_runtime(tmp_path):
    def forbidden(*args):
        pytest.fail("Preparation must not fork or infer")

    prepared = reflector(forbidden, tmp_path).prepare(SOURCE, prompt="Reflect on this conversation.")
    assert set(prepared) == {"prompt", "manifest"}
    assert prepared["prompt"] == "Reflect on this conversation."
    assert prepared["manifest"]["source"] == SOURCE.model_dump()
    assert not list(tmp_path.iterdir())


def test_native_reference_uses_native_not_canonical_id():
    session = Session(id="canonical", source_id="indexed", harness="codex", native_id="native-source")
    assert NativeSessionRef.from_session(session) == SOURCE


def test_forked_history_is_evidence_and_prompt_is_appended_once(tmp_path):
    original = [{"role": "user", "text": "Original task"},
                {"kind": "tool_call", "arguments": "historical command"},
                {"kind": "tool_result", "text": "historical result"},
                {"role": "assistant", "text": "Original answer"}]
    sessions = {SOURCE.session_id: copy.deepcopy(original)}

    def runner(source, prompt, config, on_fork):
        assert source == SOURCE and config.model == "reflector-model"
        sessions["native-reflection"] = copy.deepcopy(sessions[source.session_id])
        on_fork(ForkReceipt(source_session_id=source.session_id, session_id="native-reflection"))
        assert next(tmp_path.glob("*/fork.json")).is_file()
        sessions["native-reflection"].extend([{"role": "user", "text": prompt},
                                              {"role": "assistant", "text": "Reflection"}])
        return {"status": "completed", "thread_id": "native-reflection", "final_response": "Reflection"}

    result = reflector(runner, tmp_path).reflect(SOURCE, prompt="RP")
    assert sessions[SOURCE.session_id] == original
    assert sessions[result.fork.session_id] == original + [
        {"role": "user", "text": "RP"}, {"role": "assistant", "text": "Reflection"},
    ]
    folder = tmp_path / result.id
    assert (folder / "reflection.txt").read_text() == "Reflection"
    assert json.loads((folder / "handoff.json").read_text())["fork"]["session_id"] == "native-reflection"
    records = [json.loads(line) for line in (folder / "run.jsonl").read_text().splitlines()]
    assert [r["event"] for r in records] == ["handoff_started", "session_forked", "handoff_completed"]
    shown = CliRunner().invoke(main, ["reflection", "show", str(folder)])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["reflections"][0]["reflection"] == "Reflection"


@pytest.mark.parametrize("mode,code", [
    ("same_session", "invalid_fork"), ("wrong_parent", "invalid_fork"),
    ("missing_fork", "invalid_fork"), ("wrong_result", "invalid_fork"),
    ("duplicate_fork", "invalid_fork"), ("empty", "invalid_output"),
    ("incomplete", "incomplete"), ("exception", "runtime_error"),
])
def test_failures_leave_a_journal_and_never_a_success_artifact(tmp_path, mode, code):
    inferred = []

    def runner(source, prompt, config, on_fork):
        if mode != "missing_fork":
            receipt = ForkReceipt(source_session_id="wrong" if mode == "wrong_parent" else source.session_id,
                                  session_id=source.session_id if mode == "same_session" else "fork")
            on_fork(receipt)
            if mode == "duplicate_fork":
                on_fork(receipt)
        inferred.append(1)
        if mode == "exception":
            raise ReflectionError("runtime_error", "fixture")
        return {"status": "failed" if mode == "incomplete" else "completed",
                "thread_id": "wrong" if mode == "wrong_result" else "fork",
                "final_response": " " if mode == "empty" else "Reflection"}

    with pytest.raises(ReflectionError) as exc:
        reflector(runner, tmp_path).reflect(SOURCE)
    assert exc.value.code == code
    assert not list(tmp_path.glob("*/handoff.json"))
    if mode in {"same_session", "wrong_parent", "duplicate_fork"}:
        assert inferred == []
    records = [json.loads(line) for line in next(tmp_path.glob("*/run.jsonl")).read_text().splitlines()]
    assert records[-1]["event"] == "handoff_failed"


def test_unsupported_runtime_and_empty_prompt_fail_before_artifacts(tmp_path):
    with pytest.raises(ReflectionError, match="not implemented"):
        create_handoff_reflector("antigravity", CONFIG)
    r = reflector(lambda *a: pytest.fail("Must not execute"), tmp_path)
    with pytest.raises(ReflectionError, match="source session runtime"):
        r.reflect(NativeSessionRef(runtime="claude", session_id="native"))
    with pytest.raises(ValueError, match="nonempty"):
        r.reflect(SOURCE, prompt=" ")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", [["reflection", "handoff"], ["handoff", "reflect"]])
def test_cli_preview_and_execution_append_exact_prompt(tmp_path, monkeypatch, command):
    prompt_file = tmp_path / "rp.txt"
    prompt_file.write_text("RP\nverbatim\n")
    calls = []

    def runner(source, prompt, config, on_fork):
        calls.append((source.session_id, prompt))
        on_fork(ForkReceipt(source_session_id=source.session_id, session_id="native-reflection"))
        return {"status": "completed", "thread_id": "native-reflection", "final_response": "Reflection"}

    monkeypatch.setattr(codex, "_run_handoff", runner)
    common = [*command, "--backend", "codex", "--session-id", "native-source", "--model", "reflector-model",
              "--prompt-file", str(prompt_file)]
    cli = CliRunner()
    preview = cli.invoke(main, [*common, "--output", str(tmp_path / "preview")])
    assert preview.exit_code == 0, preview.output
    assert calls == []
    saved = json.loads((tmp_path / "preview" / "preview.json").read_text())
    assert saved["prompt"] == prompt_file.read_text()
    executed = cli.invoke(main, [*common, "--output", str(tmp_path / "run"), "--execute"])
    assert executed.exit_code == 0, executed.output
    assert calls == [("native-source", prompt_file.read_text())]
    assert json.loads(executed.output)["fork"]["session_id"] == "native-reflection"


@pytest.mark.parametrize("mode,expected_error", [
    ("success", None), ("same_id", "invalid_fork"), ("wrong_parent", "invalid_fork"),
    ("ephemeral", "invalid_fork"), ("missing_history", "missing_history"),
    ("active", "source_active"), ("active_turn", "source_active"),
    ("wrong_boundary", "invalid_fork"), ("not_persisted", "incomplete"),
    ("fork_error", "sdk_error"), ("tool", "isolation"),
])
def test_codex_native_fork_boundary_without_inference(tmp_path, monkeypatch, mode, expected_error):
    sdk = pytest.importorskip("openai_codex")
    import openai_codex.client

    calls = []
    source_bytes = b"original native history\n"
    original = tmp_path / "original.jsonl"
    original.write_bytes(source_bytes)
    source = SimpleNamespace(
        id=SOURCE.session_id, path=str(original), ephemeral=False,
        status=SimpleNamespace(root=SimpleNamespace(type="active" if mode == "active" else "idle")),
        model_dump=lambda **kw: {"cwd": str(tmp_path)},
    )
    child = SimpleNamespace(id=SOURCE.session_id if mode == "same_id" else "native-fork",
                            forked_from_id="wrong" if mode == "wrong_parent" else SOURCE.session_id,
                            ephemeral=mode == "ephemeral", path=str(tmp_path / "child.jsonl"))
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
                data = [] if mode == "missing_history" else [SimpleNamespace(
                    id=turn, status=SimpleNamespace(value="inProgress" if mode == "active_turn" else "completed"),
                )]
                return SimpleNamespace(data=data)
            if method == "mcpServerStatus/list":
                return SimpleNamespace(data=[], next_cursor=None)
            assert method == "config/read"
            return SimpleNamespace(config=SimpleNamespace(model_dump=lambda **kw: {
                "features": dict.fromkeys(codex._DISABLED_FEATURES, False), "mcp_servers": {"external": {}},
                "project_doc_max_bytes": 0, "web_search": "disabled", "skills": {"include_instructions": False},
            }))

        def thread_fork(self, thread_id, params):
            calls.append("fork")
            assert thread_id == SOURCE.session_id
            assert params["lastTurnId"] == "source-turn" and params["ephemeral"] is False
            assert params["model"] == CONFIG.model
            assert params["config"]["mcp_servers"]["external"]["enabled"] is False
            assert "baseInstructions" not in params and "developerInstructions" not in params
            if mode == "fork_error":
                raise RuntimeError("fixture")
            return SimpleNamespace(thread=child, instruction_sources=[],
                                   sandbox=SimpleNamespace(root=SimpleNamespace(type="readOnly")))

        def close(self):
            calls.append("close")

    def run(self, prompt, *, output_schema, effort):
        nonlocal completed
        assert self.id == child.id != SOURCE.session_id
        assert calls[-1] == "receipt"
        assert prompt == "RP only" and output_schema is None
        calls.append("RP")
        completed = True
        return SimpleNamespace(status=SimpleNamespace(value="completed"), final_response="Reflection",
                               id="reflection-turn", usage=None,
                               items=[SimpleNamespace(type="commandExecution")] if mode == "tool" else [])

    monkeypatch.setattr(openai_codex.client, "CodexClient", Client)
    monkeypatch.setattr(sdk.Thread, "run", run)
    if expected_error:
        with pytest.raises(ReflectionError) as exc:
            codex._run_handoff(SOURCE, "RP only", CONFIG, lambda receipt: calls.append("receipt"))
        assert exc.value.code == expected_error
        if mode not in {"not_persisted", "tool"}:
            assert "RP" not in calls
    else:
        result = codex._run_handoff(SOURCE, "RP only", CONFIG, lambda receipt: calls.append("receipt"))
        assert result["thread_id"] == child.id
        assert calls == ["start", "fork", "receipt", "RP", "close"]
    assert calls[-1] == "close"
    assert original.read_bytes() == source_bytes


def test_trajectory_boundary_is_part_of_preview_and_receipt_validation(tmp_path):
    source = SOURCE.model_copy(update={"through_turn_id": "selected-turn"})

    def runner(source, prompt, config, on_fork):
        on_fork(ForkReceipt(source_session_id=source.session_id, session_id="child", source_turn_id="later-turn"))
        pytest.fail("Wrong boundary must be rejected before inference")

    engine = reflector(runner, tmp_path)
    assert engine.prepare(source, prompt="RP")["manifest"]["source"]["through_turn_id"] == "selected-turn"
    with pytest.raises(ReflectionError, match="trajectory boundary"):
        engine.reflect(source, prompt="RP")


def test_select_source_turn_searches_pages_and_rejects_unknown_boundary():
    pytest.importorskip("openai_codex")
    calls = []

    class Client:
        def request(self, method, params, **kwargs):
            calls.append(params)
            if params["limit"] == 1:
                return SimpleNamespace(data=[SimpleNamespace(id="latest", status=SimpleNamespace(value="completed"))])
            cursor = params["cursor"]
            return SimpleNamespace(data=[SimpleNamespace(id="older" if cursor else "latest",
                                                         status=SimpleNamespace(value="completed"))],
                                   next_cursor=None if cursor else "page2")

    assert codex._source_turn(Client(), "source", "older") == "older"
    assert calls[-1]["cursor"] == "page2"
    with pytest.raises(ReflectionError, match="does not belong"):
        codex._source_turn(Client(), "source", "unknown")
