import json
import sys
from types import ModuleType

import pytest
from click.testing import CliRunner

from thearc import MetaAgent, Skill
from thearc.cli import main
from thearc.learning import SessionStore, SourceConfig
from thearc.learning.reflection import AgentReflector


@pytest.fixture
def inputs(tmp_path):
    agent = tmp_path / "agent.json"
    agent.write_text(MetaAgent(name="example", skills=[Skill(name="guide", instructions="Inspect sources.")])
                     .model_dump_json())
    source = tmp_path / "source"
    source.mkdir()
    events = [{"type": "session_meta", "payload": {"id": "thread"}},
              {"type": "response_item", "payload": {"type": "message", "role": "assistant",
               "content": [{"type": "output_text", "text": "Inspected source."}]}}]
    (source / "rollout-test.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n")
    index = tmp_path / "index.sqlite"
    with SessionStore(index) as store:
        store.ingest(SourceConfig(id="test", harness="codex", root=source))
    return ["reflection", "run", "--agent", str(agent), "--index", str(index),
            "--source-id", "test", "--model", "explicit-model", "--output", str(tmp_path / "output")]


@pytest.mark.parametrize("backend", ["antigravity", "claude", "codex"])
def test_builtin_preview_is_explicit_and_never_executes(inputs, tmp_path, monkeypatch, backend):
    def forbidden(*args):
        pytest.fail("Preview invoked an agent")

    monkeypatch.setattr(AgentReflector, "reflect", forbidden)
    result = CliRunner().invoke(main, [*inputs, "--backend", backend])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["status"] == "prepared"
    manifest = json.loads(next((tmp_path / "output").rglob("manifest.json")).read_text())
    assert manifest["backend"]["agent"] == backend


def test_no_default_backend(inputs, tmp_path):
    result = CliRunner().invoke(main, inputs)
    assert result.exit_code == 2 and "--backend" in result.output
    assert not (tmp_path / "output").exists()


def test_custom_preview_does_not_import_adapter(inputs):
    result = CliRunner().invoke(main, [*inputs, "--backend", "custom", "--runtime", "pi",
                                      "--runner", "not_installed:run", "--adapter-version", "1"])
    assert result.exit_code == 0, result.output
    assert "not_installed" not in sys.modules


def test_custom_execute_show_resume(inputs, tmp_path, monkeypatch):
    adapter = ModuleType("example_adapter")
    calls = []

    def run(prompt, schema, config):
        calls.append(prompt)
        return {"status": "completed", "final_response": json.dumps({
            "summary": "Synthetic reflection", "items": [], "limitations": ["Fixture only"],
        })}

    adapter.run = run
    monkeypatch.setitem(sys.modules, "example_adapter", adapter)
    args = [*inputs, "--backend", "custom", "--runtime", "pi", "--runner", "example_adapter:run",
            "--adapter-version", "1", "--execute"]
    runner = CliRunner()
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "completed"
    assert len(calls) == 1
    assert runner.invoke(main, [*args, "--resume"]).exit_code == 0
    assert len(calls) == 1
    shown = runner.invoke(main, ["reflection", "show", str(tmp_path / "output")])
    finding = json.loads(shown.output)["reflections"][0]
    assert finding["summary"] == "Synthetic reflection"
    assert finding["limitations"] == ["Fixture only"]
    assert runner.invoke(main, ["reflection", "show", str(tmp_path / "output"),
                                "--reflection-id", finding["id"]]).exit_code == 0
    assert runner.invoke(main, ["reflection", "show", str(tmp_path / "output"),
                                "--reflection-id", "absent"]).exit_code == 1


@pytest.mark.parametrize("options", [
    ["--backend", "custom"],
    ["--backend", "claude", "--runner", "wrong:run"],
    ["--backend", "custom", "--runtime", "pi", "--runner", "bad-address", "--adapter-version", "1"],
])
def test_invalid_runtime_options_fail_before_output(inputs, tmp_path, options):
    result = CliRunner().invoke(main, [*inputs, *options])
    assert result.exit_code == 1, result.output
    assert not (tmp_path / "output").exists()


def test_missing_custom_adapter_is_a_failed_batch(inputs):
    result = CliRunner().invoke(main, [*inputs, "--backend", "custom", "--runtime", "pi",
                                      "--runner", "not_installed:run", "--adapter-version", "1", "--execute"])
    assert result.exit_code == 1
    assert "adapter_unavailable" in result.output and "partial_failure" in result.output
