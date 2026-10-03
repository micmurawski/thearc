import json

import pytest

from thearc import MetaAgent, ResourceTarget, Skill
from thearc.learning import Reflection, ReflectionItem
from thearc.learning.curation import AgentCurator, epoch_history, load_epoch, run_curation
from thearc.learning.curation.epochs import commit_epoch
from thearc.learning.evidence.snapshot import content_hash
from thearc.models import Ranks


@pytest.fixture
def agent():
    return MetaAgent(name="graphify", version="1.0.0", skills=[
        Skill(name="graphify", instructions="# Query\n\nQuery the graph.",
              files={"references/query.md": "# Query\n\nBroad query.",
                     ".graphify_version": "0.9.68"}, ranks=Ranks(helpful=2)),
    ])


def reflection(agent, identity="reflection", event="event"):
    return Reflection(id=identity, session_ids=["session"], items=[
        ReflectionItem(target=ResourceTarget(kind="skill_file", name="graphify/references/query.md"),
                       rating="harmful", reason="Broad queries wasted time", limitations=[],
                       evidence=[{"session_id": "session", "event_id": event}]),
    ], raw={"manifest": {"config_sha256": content_hash(agent.without_ranks().model_dump(mode="json"))}})


def edit(tools, *, target=None, value="# Query\n\nUse a focused query.", ids=None):
    return tools.call("arc_change", {
        "target_json": (target or ResourceTarget(kind="skill_file",
                                                name="graphify/references/query.md")).model_dump_json(),
        "operation": "EDIT", "value_json": json.dumps(value),
        "reason": "Narrow queries to avoid the observed broad scan.",
        "reflection_ids": ids or ["reflection"],
    })


def runner(prompt, schema, tools):
    assert "Historical ranks" in prompt
    assert tools.call("arc_list", {})
    target = ResourceTarget(kind="skill_file", name="graphify/references/query.md").model_dump_json()
    assert tools.call("arc_read", {"target_json": target, "offset": 0})["current_ranks"]["harmful"] == 1
    assert tools.call("arc_history", {"target_json": target, "offset": 0})["records"]
    edit(tools)
    return {"status": "completed", "final_response": '{"summary":"Focus Graphify queries."}'}


def no_change(prompt, schema, tools):
    target = ResourceTarget(kind="skill_file", name="graphify/references/query.md").model_dump_json()
    tools.call("arc_read", {"target_json": target, "offset": 0})
    tools.call("arc_history", {"target_json": target, "offset": 0})
    return {"status": "completed", "final_response": '{"summary":"Inspected evidence; retain content."}'}


def test_edit_import_commit_and_revision_history(agent, tmp_path):
    before = agent.model_dump()
    epoch = run_curation(agent, [reflection(agent)], AgentCurator(runner), output=tmp_path,
                         epoch_id="first", result_version="1.0.1")
    assert agent.model_dump() == before
    assert load_epoch(tmp_path) == epoch
    assert len(epoch["changes"]) == 1
    change = epoch["changes"][0]
    assert change["operation"] == "EDIT"
    assert change["reflection_ids"] == ["reflection"]
    assert change["before_revision"] != change["after_revision"]
    result = MetaAgent.model_validate(epoch["agent"])
    assert result.version == "1.0.1"
    assert result.skills["graphify"].ranks.helpful == 2  # Unassessed original counts survive.
    assert result.skills["graphify"].files[".graphify_version"] == "0.9.68"
    assert result.rank_for(ResourceTarget(kind="skill_file", name="graphify/references/query.md")).harmful == 0
    assert "focused" in result.skills["graphify"].files["references/query.md"]
    assert (tmp_path / "attempts/first/result/.graphify_version").read_text().strip() == "1.0.1"
    second = run_curation(result, [], AgentCurator(no_change), output=tmp_path, epoch_id="second")
    assert second["parent"] == "first" and not second["changes"]
    assert [item["epoch_id"] for item in epoch_history(tmp_path)] == ["first", "second"]
    assert second["ledger"] == epoch["ledger"]


@pytest.mark.parametrize("failure", ["unknown_reflection", "ranks", "script", "out_of_scope", "incomplete", "tamper"])
def test_failed_edit_never_publishes_head(agent, tmp_path, failure):
    def bad(prompt, schema, tools):
        no_change(prompt, schema, tools)
        if failure == "unknown_reflection":
            edit(tools, ids=["invented"])
        elif failure == "ranks":
            edit(tools, value="---\nranks: {helpful: 999}\n---\n# Query")
        elif failure == "script":
            edit(tools, target=ResourceTarget(kind="skill", name="graphify"),
                 value={"files": {"script.py": "malicious"}})
        elif failure == "out_of_scope":
            tools.call("shell", {"command": "touch outside"})
        elif failure == "tamper":
            (tools.candidate_path / "skills/graphify/instructions.md").write_text("Unexplained change")
        else:
            edit(tools)
            return {"status": "failed"}
        return {"status": "completed", "final_response": '{"summary":"Changes"}'}
    with pytest.raises(ValueError):
        run_curation(agent, [reflection(agent)], AgentCurator(bad), output=tmp_path, epoch_id="bad")
    assert load_epoch(tmp_path) is None
    assert (tmp_path / "attempts/bad/failure.json").exists()


def test_stale_baseline_rejected_before_model_call(agent, tmp_path):
    run_curation(agent, [reflection(agent)], AgentCurator(runner), output=tmp_path, epoch_id="first")
    with pytest.raises(ValueError, match="differs from HEAD"):
        run_curation(agent, [], AgentCurator(lambda *a: pytest.fail("must not invoke")),
                     output=tmp_path, epoch_id="stale")


def test_atomic_head_failure_leaves_previous_epoch(agent, tmp_path, monkeypatch):
    from thearc.learning.curation import epochs

    original = run_curation(agent, [reflection(agent)], AgentCurator(runner), output=tmp_path, epoch_id="first")
    candidate = MetaAgent.model_validate(original["agent"])
    monkeypatch.setattr(epochs.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("injected failure")))
    with pytest.raises(OSError):
        commit_epoch(tmp_path, epoch_id="orphan", expected_head="first", baseline=candidate, agent=candidate,
                     ledger=original["ledger"], reflections=[], changes=[], actor={}, summary="No change")
    assert load_epoch(tmp_path) == original
    assert len(epoch_history(tmp_path)) == 1
    assert not (tmp_path / ".commit-lock").exists()


def test_lock_and_hash_validation(agent, tmp_path):
    run_curation(agent, [reflection(agent)], AgentCurator(runner), output=tmp_path, epoch_id="first")
    current = load_epoch(tmp_path)
    candidate = MetaAgent.model_validate(current["agent"])
    (tmp_path / ".commit-lock").mkdir()
    with pytest.raises(ValueError, match="writer"):
        commit_epoch(tmp_path, epoch_id="second", expected_head="first", baseline=candidate, agent=candidate,
                     ledger=current["ledger"], reflections=[], changes=[], actor={}, summary="No change")
    path = tmp_path / "epochs/first.json"
    data = json.loads(path.read_text())
    data["epoch"]["agent"]["version"] = "tampered"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_epoch(tmp_path)


def test_tools_closed_after_runner_finishes(agent, tmp_path):
    captured = []
    def capture(prompt, schema, tools):
        captured.append(tools)
        return no_change(prompt, schema, tools)
    run_curation(agent, [], AgentCurator(capture), output=tmp_path, epoch_id="noop")
    with pytest.raises(ValueError, match="closed"):
        captured[0].call("arc_list", {})


def test_no_tools_is_failure_not_successful_noop(agent, tmp_path):
    with pytest.raises(ValueError, match="must successfully inspect"):
        run_curation(agent, [], AgentCurator(lambda *a: {
            "status": "completed", "final_response": '{"summary":"Tools unavailable, no change."}',
        }), output=tmp_path, epoch_id="unavailable")
    assert load_epoch(tmp_path) is None


def test_cli_inspection_is_offline(agent, tmp_path):
    from click.testing import CliRunner

    from thearc.cli import main

    run_curation(agent, [reflection(agent)], AgentCurator(runner), output=tmp_path, epoch_id="first")
    for command in ("show", "history"):
        result = CliRunner().invoke(main, ["curation", command, str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "first" in result.output and "graphify" in result.output


def test_scoped_tools_require_known_arguments_and_obey_budget(agent, tmp_path):
    from thearc.learning.curation import AssessmentLedger
    from thearc.learning.curation.tools import CurationTools

    tools = CurationTools(agent, [], AssessmentLedger(), [], tmp_path, max_calls=1)
    with pytest.raises(ValueError, match="unexpected arguments"):
        tools.call("arc_read", {"target_json": "{}", "offset": 0, "path": "/outside"})
    tools.call("arc_list", {})
    with pytest.raises(ValueError, match="budget"):
        tools.call("arc_list", {})


def test_codex_curator_uses_scoped_callback_contract(agent, tmp_path):
    from thearc.learning.curation import CodexCurator, CodexCuratorConfig

    def sdk(prompt, schema, config, *, instructions, scoped_tools):
        assert config.model == "test-model" and "curator" in instructions
        return runner(prompt, schema, scoped_tools)
    result = run_curation(agent, [reflection(agent)],
                          CodexCurator(CodexCuratorConfig(model="test-model"), sdk_runner=sdk),
                          output=tmp_path, epoch_id="sdk")
    assert result["actor"]["backend"] == "codex"
    assert len(result["changes"]) == 1
