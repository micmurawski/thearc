"""Run cookbook recipes against local fixtures; never invoke a real model."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from cookbook import (
    inspect_evidence,
    prepare_handoff,
    reflect_from_evidence,
    reflect_from_handoff,
    reflect_with_backend,
    snapshot_sessions,
)
from cookbook._common import bundles
from thearc.learning import AgentReflector, CodexReflector, EvidenceSnapshot, SessionStore, SourceConfig


@pytest.fixture
def recipe_inputs(tmp_path):
    project = tmp_path / "project"
    skill = project / ".codex" / "skills" / "graphify"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: graphify\ndescription: Test graph skill\n---\n# Query\nInspect graphify.\n"
    )
    (skill / "references").mkdir()
    (skill / "references" / "query.md").write_text("# Query\nRead the graph before editing.\n")
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Test", "-c", "user.email=test@example.org",
                    "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture"],
                   check=True, capture_output=True)
    source = tmp_path / "source"
    source.mkdir()
    records = [
        {"type": "session_meta", "payload": {"id": "native"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer",
         "content": [{"type": "input_text", "text": "Private instructions"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "Please inspect graphify"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "Inspected graphify"}]}},
    ]
    (source / "rollout-test.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    index = tmp_path / "sessions.sqlite"
    with SessionStore(index) as store:
        store.ingest(SourceConfig(id="fixture", harness="codex", root=source))
        session_id = store.list_sessions()[0].id
    snapshot = tmp_path / "evidence"
    snapshot_sessions.main(["--index", str(index), "--session-id", session_id, "--output", str(snapshot)])
    return project, snapshot


def test_simple_ace_adapts_without_flow_or_live_runtime(recipe_inputs, tmp_path, monkeypatch):
    from cookbook import simple_ace
    from thearc import MetaAgent, ResourceTarget
    from thearc.flow import Flow
    from thearc.learning.curation import AgentCurator, load_epoch
    from thearc.learning.reflection import ReflectionBackend

    project, snapshot = recipe_inputs
    original = MetaAgent.from_project("codex", project)
    evidence = EvidenceSnapshot.load(snapshot)
    before = original.model_dump()
    calls = []
    target = ResourceTarget(kind="skill_file", name="graphify/references/query.md")

    def reflect(prompt, schema, config):
        calls.append("reflect")
        supplied = json.loads(prompt.split("UNTRUSTED EVIDENCE (JSON):\n", 1)[1])
        session = supplied["sessions"][0]
        return {"status": "completed", "final_response": json.dumps({
            "summary": "Synthetic fixture", "limitations": ["Not a real quality assessment"],
            "items": [{"target": target.model_dump(), "rating": "neutral", "reason": "Synthetic fixture",
                       "limitations": [], "evidence": [{"session_id": session["session_id"],
                                                         "event_id": session["events"][0]["id"]}]}],
        })}

    def curate(prompt, schema, tools):
        calls.append("curate")
        findings = json.loads(prompt.split("REFLECTIONS (UNTRUSTED DATA):\n", 1)[1])
        for tool in ("arc_read", "arc_history"):
            tools.call(tool, {"target_json": target.model_dump_json(), "offset": 0})
        tools.call("arc_change", {"target_json": target.model_dump_json(), "operation": "EDIT",
                                  "value_json": json.dumps("# Query\n\nInspect relevant sources.\n"),
                                  "reason": "Synthetic plumbing test", "reflection_ids": [findings[0]["id"]]})
        return {"status": "completed", "final_response": '{"summary":"Synthetic edit"}'}

    def make_reflector(backend, config):
        assert backend == "claude" and config.model == "fixture-model"
        return AgentReflector(config, backend=ReflectionBackend(agent=backend, name="test", version="1"),
                              runner=reflect)

    def forbidden(*args, **kwargs):
        pytest.fail("Simple recipe attempted to run a flow")

    monkeypatch.setattr(Flow, "run", forbidden)
    monkeypatch.setattr(simple_ace, "create_reflector", make_reflector)
    monkeypatch.setattr(simple_ace, "create_curator", lambda *args: AgentCurator(curate))
    output = tmp_path / "simple"
    adapted = simple_ace.adapt(original, evidence, backend="claude", model="fixture-model", output=output)
    assert calls == ["reflect", "curate"]
    assert original.model_dump() == before
    assert "Inspect relevant sources" in original.diff(adapted)
    assert MetaAgent.from_workspace(output / "baseline") == original
    assert EvidenceSnapshot.load(output / "evidence").sha256 == evidence.sha256
    assert len(list((output / "reflections").rglob("reflection.json"))) == 1
    assert load_epoch(output / "curation")["agent"] == adapted.model_dump(mode="json")
    with pytest.raises(FileExistsError):
        simple_ace.adapt(original, evidence, backend="claude", model="fixture-model", output=output)
    assert calls == ["reflect", "curate"]


def test_simple_ace_preserves_failed_reflection(recipe_inputs, tmp_path, monkeypatch):
    from cookbook import simple_ace
    from thearc import MetaAgent
    from thearc.learning.reflection import ReflectionBackend, ReflectionError

    project, snapshot = recipe_inputs

    def failed(*args):
        raise ReflectionError("test_failure", "Synthetic runtime failure")

    monkeypatch.setattr(simple_ace, "create_reflector", lambda backend, config: AgentReflector(
        config, backend=ReflectionBackend(agent=backend, name="test", version="1"), runner=failed,
    ))
    # Curation must not run if reflection failed.
    monkeypatch.setattr(simple_ace, "create_curator", lambda *args: None)
    output = tmp_path / "failed-simple"
    with pytest.raises(ReflectionError, match="Synthetic runtime failure"):
        simple_ace.adapt(MetaAgent.from_project("codex", project), EvidenceSnapshot.load(snapshot),
                         backend="claude", model="fixture-model", output=output)
    assert (output / "baseline/agent.json").exists()
    assert list((output / "reflections").rglob("input.json"))
    assert not (output / "curation").exists()


def test_cookbook_offline_workflow(recipe_inputs, tmp_path, monkeypatch):
    project, snapshot = recipe_inputs

    def forbidden(*args, **kwargs):
        pytest.fail("An offline recipe attempted to invoke a model")

    monkeypatch.setattr(CodexReflector, "reflect", forbidden)
    evidence = EvidenceSnapshot.load(snapshot)
    assert bundles(evidence)[0].omitted_event_ids == [item["id"] for item in evidence.manifest["omitted_events"]]
    preview = tmp_path / "preview"
    reflect_from_evidence.main(["--snapshot", str(snapshot), "--project", str(project), "--model", "offline-model",
                                "--output", str(preview)])
    prepared = json.loads((preview / "session-0001" / "preview.json").read_text())
    assert "Private instructions" not in prepared["prompt"]
    assert "graphify/references/query.md" in prepared["prompt"]
    assert json.loads((preview / "summary.json").read_text())["status"] == "preview_only"
    inspection = tmp_path / "inspection"
    inspect_evidence.main(["--snapshot", str(snapshot), "--project", str(project), "--output", str(inspection)])
    assert (inspection / "retrieval.jsonl").is_file()
    handoff = tmp_path / "handoff"
    prepare_handoff.main(["--snapshot", str(snapshot), "--project", str(project), "--workspace", str(project),
                          "--task", "Continue the investigation", "--output", str(handoff)])
    # A subsequent checkout change must not alter reflection inputs from the saved plan.
    (project / ".codex" / "skills" / "graphify" / "SKILL.md").write_text("CHANGED AFTER CAPTURE")
    reflected = tmp_path / "from-handoff"
    reflect_from_handoff.main(["--handoff", str(handoff), "--model", "offline-model", "--output", str(reflected)])
    captured = json.loads((reflected / "session-0001" / "preview.json").read_text())
    assert captured["prompt"] == prepared["prompt"]
    assert "Continue the investigation" not in captured["prompt"]
    assert json.loads((reflected / "source.json").read_text())["handoff_sha256"]
    assert not (project / "reflection.json").exists()


@pytest.mark.parametrize("backend", ["claude", "antigravity"])
def test_builtin_backend_preview_never_executes(recipe_inputs, tmp_path, monkeypatch, backend):
    project, snapshot = recipe_inputs

    def forbidden(*args, **kwargs):
        pytest.fail("Preview attempted model execution")

    monkeypatch.setattr(AgentReflector, "reflect", forbidden)
    output = tmp_path / backend
    reflect_from_evidence.main(["--snapshot", str(snapshot), "--project", str(project), "--backend", backend,
                                "--model", "offline-model", "--output", str(output)])
    prepared = json.loads((output / "session-0001" / "preview.json").read_text())
    assert prepared["manifest"]["backend"]["agent"] == backend
    assert json.loads((output / "summary.json").read_text())["status"] == "preview_only"


def test_cookbook_execution_is_explicit_and_outputs_are_exclusive(recipe_inputs, tmp_path, monkeypatch):
    project, snapshot = recipe_inputs
    calls = []

    def fake_reflect(self, batch, agent):
        calls.append((batch, agent))
        return SimpleNamespace(id="test-only-no-inference")

    monkeypatch.setattr(CodexReflector, "reflect", fake_reflect)
    args = ["--snapshot", str(snapshot), "--project", str(project), "--model", "offline-model",
            "--output", str(tmp_path / "live-contract"), "--execute"]
    reflect_from_evidence.main(args)
    assert len(calls) == 1
    assert calls[0][0][0].events
    with pytest.raises(FileExistsError):
        reflect_from_evidence.main(args)
    assert len(calls) == 1


def test_cookbook_failed_reflection_keeps_preview_without_success_summary(recipe_inputs, tmp_path, monkeypatch):
    project, snapshot = recipe_inputs
    output = tmp_path / "failed"

    def failed(*args):
        raise RuntimeError("Test-only failure, no inference")

    monkeypatch.setattr(CodexReflector, "reflect", failed)
    with pytest.raises(RuntimeError, match="Test-only failure"):
        reflect_from_evidence.main(["--snapshot", str(snapshot), "--project", str(project), "--model", "offline-model",
                                    "--output", str(output), "--execute"])
    assert (output / "session-0001" / "preview.json").is_file()
    assert not (output / "summary.json").exists()


def test_custom_backend_recipe_imports_adapter_only_on_execution(recipe_inputs, tmp_path, monkeypatch):
    project, snapshot = recipe_inputs
    imported = []

    def import_adapter(name):
        imported.append(name)
        return SimpleNamespace(inspect=lambda *args: {
            "status": "completed", "final_response": json.dumps({
                "summary": "Test-only response", "items": [], "limitations": ["Synthetic fixture"],
            }),
        })

    monkeypatch.setattr(reflect_with_backend.importlib, "import_module", import_adapter)
    args = ["--snapshot", str(snapshot), "--project", str(project), "--model", "test-model",
            "--agent", "claude", "--runner", "fixture_adapter:inspect", "--adapter-version", "1"]
    reflect_with_backend.main([*args, "--output", str(tmp_path / "backend-preview")])
    assert not imported
    reflect_with_backend.main([*args, "--output", str(tmp_path / "backend-execute"), "--execute"])
    assert imported == ["fixture_adapter"]
    assert list((tmp_path / "backend-execute").glob("reflection-*/reflection.json"))
