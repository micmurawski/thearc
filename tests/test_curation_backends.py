import json
import sys
from types import ModuleType

import pytest
from click.testing import CliRunner

from thearc import ContextDocument, MetaAgent, ResourceTarget
from thearc.cli import main
from thearc.learning import Reflection, ReflectionItem
from thearc.learning.curation import (
    AntigravityCurator,
    AntigravityCuratorConfig,
    ClaudeCurator,
    ClaudeCuratorConfig,
    create_curator,
    run_curation,
)
from thearc.learning.curation.structured import StructuredCurator
from thearc.learning.evidence.snapshot import content_hash
from thearc.learning.reflection import ReflectorConfig


def inputs():
    agent = MetaAgent(name="example", context=[ContextDocument(filename="AGENTS.md", content="Read everything.")])
    target = ResourceTarget(kind="context", name="AGENTS.md")
    finding = Reflection(id="r1", session_ids=["s1"], items=[ReflectionItem(
        target=target, rating="harmful", reason="Unfocused inspection", limitations=[],
        evidence=[{"session_id": "s1", "event_id": "e1"}],
    )], raw={"manifest": {"config_sha256": content_hash(agent.without_ranks().model_dump(mode="json"))}})
    return agent, target, finding


def step(tool, arguments=None, summary=""):
    return {"status": "completed", "final_response": json.dumps({
        "tool": tool, "arguments_json": json.dumps(arguments or {}), "summary": summary,
    }), "thread_id": "synthetic-thread"}


def edit_runner(target):
    sequence = [step("arc_list"), step("arc_read", {"target_json": target.model_dump_json(), "offset": 0}),
                step("arc_history", {"target_json": target.model_dump_json(), "offset": 0}),
                step("arc_change", {"target_json": target.model_dump_json(), "operation": "EDIT",
                                    "value_json": json.dumps({"content": "Read relevant sources first."}),
                                    "reason": "Focus inspection based on evidence.", "reflection_ids": ["r1"]}),
                step("finish", summary="Focused source inspection.")]
    calls = []

    def run(prompt, schema, config):
        assert "HOST CAPABILITIES" in prompt and "UNTRUSTED DATA" in prompt
        assert schema["properties"]["tool"]["enum"]
        if calls:
            assert '"result"' in prompt
        calls.append(prompt)
        return sequence[len(calls) - 1]

    return run


@pytest.mark.parametrize("curator_type,config_type,backend", [
    (ClaudeCurator, ClaudeCuratorConfig, "claude"),
    (AntigravityCurator, AntigravityCuratorConfig, "antigravity"),
])
def test_bundled_curators_scoped_roundtrip(tmp_path, curator_type, config_type, backend):
    agent, target, finding = inputs()
    curator = curator_type(config_type(model="test"), runner=edit_runner(target))
    epoch = run_curation(agent, [finding], curator, output=tmp_path, result_version="1.1")
    assert "Read relevant sources first." in epoch["agent"]["context"]["AGENTS.md"]["content"]
    assert epoch["changes"][0]["reflection_ids"] == ["r1"]
    assert epoch["actor"]["backend"] == backend
    assert len(epoch["actor"]["turns"]) == 5
    assert agent.context["AGENTS.md"].content == "Read everything."


@pytest.mark.parametrize("failure", ["no_inspection", "unknown_tool", "invalid_json", "incomplete", "budget"])
def test_structured_failures_never_commit(tmp_path, failure):
    agent, _, finding = inputs()
    response = {
        "no_inspection": step("finish", summary="Done"),
        "unknown_tool": step("shell"),
        "invalid_json": {"status": "completed", "final_response": "not json"},
        "incomplete": {"status": "failed"},
        "budget": step("arc_list"),
    }[failure]
    curator = StructuredCurator(ReflectorConfig(model="test"), lambda *args: response,
                                actor={"backend": "test"}, max_steps=2)
    with pytest.raises((ValueError, RuntimeError)):
        run_curation(agent, [finding], curator, output=tmp_path)
    assert not (tmp_path / "HEAD.json").exists()
    assert list(tmp_path.rglob("failure.json"))


def test_custom_pi_curation_cli(tmp_path, monkeypatch):
    agent, target, finding = inputs()
    agent_path = tmp_path / "agent.json"
    agent_path.write_text(agent.model_dump_json())
    finding_path = tmp_path / "reflection.json"
    finding_path.write_text(finding.model_dump_json())
    module = ModuleType("pi_adapter")
    module.run = edit_runner(target)
    monkeypatch.setitem(sys.modules, "pi_adapter", module)
    args = ["curation", "run", "--agent", str(agent_path), "--reflections", str(finding_path),
            "--output", str(tmp_path / "output"), "--model", "test"]
    runner = CliRunner()
    missing = runner.invoke(main, args)
    assert missing.exit_code == 2 and "--backend" in missing.output
    result = runner.invoke(main, [*args, "--backend", "custom", "--runtime", "pi", "--runner", "pi_adapter:run",
                                  "--adapter-version", "1"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["changes"]) == 1


@pytest.mark.parametrize("backend", ["claude", "antigravity", "codex"])
def test_curator_factory(backend):
    curator = create_curator(backend, ReflectorConfig(model="test"))
    assert curator.actor["backend"] == backend


def test_cli_help_does_not_recommend_runtime():
    runner = CliRunner()
    for group in ("reflection", "curation"):
        result = runner.invoke(main, [group, "run", "--help"])
        assert result.exit_code == 0
        assert "No default" in result.output
        assert "Invoke Codex" not in result.output


@pytest.mark.parametrize("backend", ["claude", "antigravity"])
def test_bundled_transport_receives_curation_instructions(tmp_path, monkeypatch, backend):
    from thearc.learning.curation import providers
    from thearc.learning.curation.engine import CURATOR_PROMPT

    agent, target, finding = inputs()
    run = edit_runner(target)

    def transport(prompt, schema, config, *, instructions):
        assert instructions.startswith(CURATOR_PROMPT)
        assert "host capability protocol" in instructions
        return run(prompt, schema, config)

    adapter = getattr(providers, backend)
    module = getattr(adapter, backend)
    monkeypatch.setattr(module, f"_run_{backend}", transport)
    epoch = run_curation(agent, [finding], create_curator(backend, ReflectorConfig(model="test")), output=tmp_path)
    assert len(epoch["changes"]) == 1


def test_structured_deadline_rejects_late_response_before_edit(tmp_path, monkeypatch):
    from thearc.learning.curation import structured

    agent, target, finding = inputs()
    times = iter([0, 0, 10])
    monkeypatch.setattr(structured.time, "monotonic", lambda: next(times))
    curator = StructuredCurator(ReflectorConfig(model="test", timeout_seconds=1), edit_runner(target),
                                actor={"backend": "test"})
    with pytest.raises(RuntimeError, match="deadline"):
        run_curation(agent, [finding], curator, output=tmp_path)
    assert not (tmp_path / "HEAD.json").exists()


def test_structured_transcript_budget(tmp_path):
    agent, target, finding = inputs()
    curator = StructuredCurator(ReflectorConfig(model="test", max_input_chars=1000), edit_runner(target),
                                actor={"backend": "test"})
    with pytest.raises((ValueError, RuntimeError), match="limit"):
        run_curation(agent, [finding], curator, output=tmp_path)
    assert not (tmp_path / "HEAD.json").exists()
