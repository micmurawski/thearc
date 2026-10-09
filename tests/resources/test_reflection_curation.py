import json

import pytest

from thearc import MetaAgent
from thearc.learning.reflection.runtime import ReflectionError
from thearc.resources import (
    CurationReport,
    Curator,
    OutputValidationError,
    Reflection,
    Reflector,
    ResourceAssessmentReport,
    Session,
)

from .conftest import event
from .test_schemas import Report


def test_prompt_schema_validation_and_failure_artifacts(agent, tmp_path):
    calls = []

    def runner(prompt, schema, config):
        calls.append(prompt)
        return {"status": "completed", "final_response": '{"findings": [{"resource": null}]}'}

    reflector = Reflector(implementation="custom", model="fixture", runner=runner, artifact_dir=tmp_path)
    result = reflector.reflect("PROMPT\nMY EVIDENCE", agent=agent, output_schema=Report, validate_resources=True)
    assert isinstance(result.output, Report)
    assert calls[0].count("MY EVIDENCE") == 1
    assert "AGENT RESOURCE CATALOG" in calls[0] and "JSON Schema" in calls[0]
    assert result.agent_revision and result.agent_mode == "reference"
    assert (tmp_path / result.id / "reflection.json").is_file()
    loaded = Reflection.load(tmp_path / result.id / "reflection.json")
    assert loaded.output["findings"] == [{"resource": None}]
    from click.testing import CliRunner

    from thearc.cli import main

    shown = CliRunner().invoke(main, ["reflection", "show", str(tmp_path / result.id)])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["reflections"][0]["id"] == result.id
    reflector.runner = lambda *args: {"status": "completed", "final_response": "invalid JSON"}
    with pytest.raises(OutputValidationError):
        reflector.reflect("prompt", output_schema=Report)
    assert len(list(tmp_path.glob("*/failure.json"))) == 1
    assert any("invalid JSON" in p.read_text() for p in tmp_path.glob("*/response.json"))


def test_prepare_has_no_execution_and_missing_agent_fails():
    reflector = Reflector(implementation="codex", model="fixture")
    assert reflector.prepare("prompt")["prompt"] == "prompt"
    with pytest.raises(ValueError, match="MetaAgent"):
        reflector.reflect("prompt", output_schema=Report, validate_resources=True)
    with pytest.raises(TypeError):
        reflector.reflect("prompt", evidence_view="compact")


def test_incomplete_tool_history_rejected_before_runtime():
    history = Session.from_events(
        [
            event(0, role="user", text="Task"),
            event(1, kind="tool_call", tool_name="shell", call_id="pending", arguments={}),
        ]
    )
    reflector = Reflector(implementation="codex", model="fixture")
    with pytest.raises(ReflectionError, match="completed tool call"):
        reflector.handoff("Reflect", history)


def test_activation_rejects_unsupported_resources_before_runner(agent):
    reflector = Reflector(implementation="codex", model="fixture")
    with pytest.raises(ReflectionError, match="context and rules"):
        reflector.prepare("prompt", agent=agent, agent_mode="active")


def test_curation_accepts_text_without_manufacturing_ranks(agent, tmp_path):
    before = agent.model_dump()
    reflector = Reflector(
        implementation="custom",
        model="fixture",
        runner=lambda *args: {"status": "completed", "final_response": "Inspect errors first."},
    )
    reflection = reflector.reflect("prompt", agent=agent)
    report = CurationReport(
        summary="Improve retry guidance",
        changes=[
            {
                "id": "change",
                "target": {"kind": "skill", "name": "debug"},
                "operation": "EDIT",
                "value": {"instructions": "Check error output before retrying."},
                "reflection_ids": [reflection.id],
                "rationale": "The reflection identified an unchanged retry.",
            }
        ],
    )
    curator = Curator(
        implementation="custom",
        model="fixture",
        runner=lambda *args: {
            "status": "completed",
            "final_response": report.model_dump_json(),
        },
    )
    result = curator.curate("Improve configuration", [reflection], agent=agent, output=tmp_path)
    assert result.agent.skills["debug"].instructions.startswith("Check error")
    assert len(result.changes) == 1
    assert agent.model_dump() == before
    assert not result.epoch["ledger"]["assessments"]
    assert list(tmp_path.glob("attempts/*/output-schema.json"))


def test_invalid_curation_cannot_commit_even_without_optional_validation(agent, tmp_path):
    report = CurationReport(
        summary="Invalid",
        changes=[
            {
                "id": "change",
                "target": {"kind": "skill", "name": "missing"},
                "operation": "REMOVE",
                "reflection_ids": ["invented"],
                "rationale": "Missing",
                "value": None,
            }
        ],
    )
    curator = Curator(
        implementation="custom",
        model="fixture",
        runner=lambda *args: {
            "status": "completed",
            "final_response": report.model_dump_json(),
        },
    )
    with pytest.raises(ValueError):
        curator.curate("prompt", [], agent=agent, validate_resources=False, output=tmp_path)
    assert not (tmp_path / "HEAD.json").exists()


def test_empty_agent_no_change():
    curator = Curator(
        implementation="custom",
        model="fixture",
        runner=lambda *args: {
            "status": "completed",
            "final_response": '{"summary":"No evidence for changes", "changes":[]}',
        },
    )
    result = curator.curate("prompt", [], agent=MetaAgent(name="empty"))
    assert not result.changes


def test_assessment_requires_explicit_evidence_verification(agent, history):
    data = {
        "summary": "Retry failure",
        "findings": [
            {
                "resource": {"kind": "skill", "name": "debug"},
                "rating": "harmful",
                "reason": "Inputs remained invalid",
                "event_ids": ["e2"],
            }
        ],
    }
    reflector = Reflector(
        implementation="custom",
        model="fixture",
        runner=lambda *args: {
            "status": "completed",
            "final_response": json.dumps(data),
        },
    )
    reflection = reflector.reflect("prompt", agent=agent, output_schema=ResourceAssessmentReport)
    verified = reflection.assessments(history)
    assert verified.items[0].evidence[0].session_id == "s"
    data["findings"][0]["event_ids"] = ["unknown"]
    reflection = reflector.reflect("prompt", agent=agent, output_schema=ResourceAssessmentReport)
    with pytest.raises(ValueError, match="outside"):
        reflection.assessments(history)


def test_evidence_tool_keeps_the_reflection_scope(history):
    from thearc.resources.curation import EvidenceTools

    tools = EvidenceTools({"reflection": history.split_into_trajectories()[0]})
    listed = tools.call("arc_evidence", {"reflection_id": "reflection", "event_id": "", "offset": 0})
    assert [e["event_id"] for e in listed["events"]] == ["e0", "e1", "e2", "e3"]
    result = tools.call("arc_evidence", {"reflection_id": "reflection", "event_id": "e2", "offset": 0})
    assert "SyntaxError" in result["content"]
    with pytest.raises(ValueError, match="outside"):
        tools.call("arc_evidence", {"reflection_id": "reflection", "event_id": "e5", "offset": 0})
