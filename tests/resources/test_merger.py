import json
from dataclasses import replace

import pytest

from thearc import ContextDocument, MetaAgent, Skill
from thearc.resources import Curator, Merger, MergeReport, Reflector
from thearc.resources.immutable import freeze, thaw


@pytest.fixture
def base():
    return MetaAgent(
        name="agent",
        skills=[
            Skill(
                name="debug",
                description="Debug",
                instructions="# Inspect\n\nRead errors.\n\n# Retry\n\nRetry once.\n",
                files={"notes.md": "# Notes\n\nObserve.\n", "run.py": "print(1)\n"},
            )
        ],
        context=[ContextDocument(filename="AGENTS.md", content="# Rules\n\nBe precise.\n")],
    )


def branch(base, *, target=None, value=None, operation="EDIT", reflection=None):
    if reflection is None:
        reflection = Reflector(
            implementation="custom",
            model="fixture",
            runner=lambda *a: {
                "status": "completed",
                "final_response": "Supported improvement.",
            },
        ).reflect("Review", agent=base)
    changes = (
        []
        if target is None
        else [
            {
                "id": "proposal",
                "target": target,
                "value": value,
                "operation": operation,
                "reflection_ids": [reflection.id],
                "rationale": "Supported by observed behavior.",
            }
        ]
    )
    return Curator(
        implementation="custom",
        model="fixture",
        runner=lambda *a: {
            "status": "completed",
            "final_response": json.dumps({"summary": "Adapt", "changes": changes}),
        },
    ).curate("Improve", [reflection], agent=base)


def edit(base, value):
    return branch(base, target={"kind": "skill", "name": "debug"}, value=value)


def test_independent_and_identical_edits_preserve_inputs(base, tmp_path):
    a = edit(base, {"description": "Investigate failures"})
    b = edit(base, {"instructions": "# Inspect\n\nRead stderr.\n"})
    c = edit(base, {"description": "Investigate failures"})
    before = base.model_dump()
    snapshots = [x.agent.model_dump() for x in [a, b, c]]
    result = Merger().merge(base=base, candidates=[a, b, c], output=tmp_path / "merge")
    assert not result.unresolved
    assert result.agent.skills["debug"].description == "Investigate failures"
    assert "Read stderr" in result.agent.skills["debug"].instructions
    assert base.model_dump() == before
    assert [x.agent.model_dump() for x in [a, b, c]] == snapshots
    assert len(result.provenance["source_epochs"]) == 3
    assert (tmp_path / "merge/merge.json").is_file()
    detached = result.agent
    detached.name = "mutated"
    assert result.agent.name == base.name
    reversed_result = Merger().merge(base=base, candidates=[c, b, a])
    assert reversed_result.agent == result.agent
    assert reversed_result.provenance == result.provenance


def test_disjoint_markdown_edits_combine(base):
    a = edit(base, {"instructions": base.skills["debug"].instructions.replace("Read errors.", "Read stderr.")})
    b = edit(base, {"instructions": base.skills["debug"].instructions.replace("Retry once.", "Retry twice.")})
    result = Merger().merge(base=base, candidates=[a, b])
    assert not result.unresolved
    assert "Read stderr." in result.agent.skills["debug"].instructions
    assert "Retry twice." in result.agent.skills["debug"].instructions


def conflicted(base):
    return [edit(base, {"description": "Choice A"}), edit(base, {"description": "Choice B"})]


def report(conflict, action="select", selected=None, changes=None):
    return MergeReport(
        resolutions=[
            {
                "conflict_id": conflict.id,
                "action": action,
                "selected_change_ids": selected if selected is not None else [conflict.proposals[0]["id"]],
                "changes": changes or [],
                "rationale": "Evidence supports this choice.",
            }
        ],
        limitations=[],
    )


def test_explicit_conflict_and_manual_selection(base):
    branches = conflicted(base)
    result = Merger().merge(base=base, candidates=branches)
    assert len(result.conflicts) == len(result.unresolved) == 1
    with pytest.raises(ValueError, match="unresolved"):
        _ = result.agent
    selected = result.conflicts[0].proposals[0]
    resolved = Merger().merge(base=base, candidates=branches, resolutions=report(result.conflicts[0]))
    assert resolved.agent.skills["debug"].description == selected["value"]
    kept = Merger().merge(
        base=base, candidates=branches, resolutions=report(result.conflicts[0], action="retain_base", selected=[])
    )
    assert kept.agent.without_ranks() == base.without_ranks()


def test_resolver_receives_schema_and_review(base, tmp_path):
    branches = conflicted(base)
    conflict = Merger().merge(base=base, candidates=branches).conflicts[0]
    calls = []

    def run(prompt, schema, config):
        calls.append(schema["title"])
        assert "Choice A" in prompt and "Choice B" in prompt
        output = (
            report(conflict).model_dump() if schema["title"] == "MergeReport" else {"issues": [], "limitations": []}
        )
        return {"status": "completed", "final_response": json.dumps(output)}

    merger = Merger(resolver=Curator(implementation="custom", model="fixture", runner=run))
    result = merger.merge(base=base, candidates=branches, output=tmp_path / "merge")
    assert not result.unresolved
    assert calls == ["MergeReport", "MergeReview"]
    assert len(list((tmp_path / "merge/resolver").glob("*/reflection.json"))) == 2


def test_consistency_review_can_block_nonoverlapping_edits(base):
    branches = [
        edit(base, {"description": "Always retry"}),
        branch(base, target={"kind": "context", "name": "AGENTS.md"}, value={"content": "Never retry"}),
    ]
    resolver = Curator(
        implementation="custom",
        model="fixture",
        runner=lambda *a: {
            "status": "completed",
            "final_response": '{"issues":["Contradictory retry guidance"],"limitations":[]}',
        },
    )
    result = Merger(resolver=resolver).merge(base=base, candidates=branches)
    assert not result.conflicts and result.unresolved[0].reason == "Contradictory retry guidance"
    with pytest.raises(ValueError):
        _ = result.agent


def test_combine_is_checked_and_scoped(base):
    branches = conflicted(base)
    conflict = Merger().merge(base=base, candidates=branches).conflicts[0]
    reflection_ids = [r["id"] for r in branches[0].epoch["reflections"]]
    change = {
        "id": "combined",
        "target": {"kind": "skill", "name": "debug"},
        "operation": "EDIT",
        "value": {"description": "Combined"},
        "reflection_ids": reflection_ids,
        "rationale": "Synthesis",
    }
    resolved = Merger().merge(
        base=base, candidates=branches, resolutions=report(conflict, "combine", selected=[], changes=[change])
    )
    assert resolved.agent.skills["debug"].description == "Combined"
    change["value"]["instructions"] = "Unrelated change"
    with pytest.raises(ValueError, match="outside"):
        Merger().merge(
            base=base, candidates=branches, resolutions=report(conflict, "combine", selected=[], changes=[change])
        )


def test_parent_delete_conflicts_with_child_edit(base):
    # Use a skill without executable files: their deletion is correctly forbidden by curation.
    base.skills["debug"].files.pop("run.py")
    a = branch(base, target={"kind": "skill", "name": "debug"}, operation="REMOVE")
    b = branch(base, target={"kind": "skill_file", "name": "debug/notes.md"}, value="Revised notes")
    result = Merger().merge(base=base, candidates=[a, b])
    assert result.conflicts[0].path == ("skills", "debug")


@pytest.mark.parametrize("mode", ["wrong_base", "duplicate", "tamper", "version"])
def test_invalid_candidates_rejected(base, mode):
    a = edit(base, {"description": "Updated"})
    candidates = [a]
    if mode == "wrong_base":
        base.skills["debug"].description = "Different"
    elif mode == "duplicate":
        candidates.append(a)
    elif mode == "version":
        base.version = "new"
    else:
        agent = a.agent
        agent.skills["debug"].instructions = "Unrecorded edit"
        epoch = thaw(a.epoch)
        epoch["agent"] = agent.model_dump(mode="json")
        candidates = [replace(a, _agent_json=agent.model_dump_json(), epoch=freeze(epoch))]
    with pytest.raises(ValueError):
        Merger().merge(base=base, candidates=candidates)


def test_invalid_resolution_ids_fail_without_success_artifact(base, tmp_path):
    branches = conflicted(base)
    conflict = Merger().merge(base=base, candidates=branches).conflicts[0]
    with pytest.raises(ValueError, match="belong"):
        Merger().merge(
            base=base,
            candidates=branches,
            resolutions=report(conflict, selected=["invented"]),
            output=tmp_path / "merge",
        )
    assert (tmp_path / "merge/failure.json").exists()
    assert not (tmp_path / "merge/merge.json").exists()


def test_empty_noop_and_unknown_resolution(base):
    with pytest.raises(ValueError):
        Merger().merge(base=base, candidates=[])
    result = Merger().merge(base=base, candidates=[branch(base)])
    assert result.agent.without_ranks() == base.without_ranks()
    assert result.changes == ()


def test_rank_baselines_and_shared_assessments_are_not_summed(base):
    from thearc.learning.curation.models import AssessmentReport, ReflectionItem
    from thearc.learning.evidence.snapshot import content_hash
    from thearc.models import Ranks

    base.skills["debug"].ranks = Ranks(helpful=4)
    observed = AssessmentReport(
        id="shared-assessment",
        session_ids=["session"],
        items=[
            ReflectionItem(
                target={"kind": "skill", "name": "debug"},
                rating="helpful",
                reason="Observed use",
                evidence=[{"session_id": "session", "event_id": "event"}],
                limitations=[],
            )
        ],
        raw={"manifest": {"config_sha256": content_hash(base.without_ranks().model_dump(mode="json"))}},
    )
    a, b = branch(base, reflection=observed), branch(base, reflection=observed)
    result = Merger().merge(base=base, candidates=[a, b])
    assert result.agent.skills["debug"].ranks.helpful == 5
    assert len(result.ledger["assessments"]) == 1
    assert len(result.ledger["baselines"]) == 1
    assert base.skills["debug"].ranks.helpful == 4
    # Separate reflections that cite overlapping evidence stay visible but count once.
    other = observed.model_copy(update={"id": "other-assessment"})
    c = branch(base, reflection=other)
    overlap = Merger().merge(base=base, candidates=[a, c])
    assert overlap.agent.skills["debug"].ranks.helpful == 5
    assert len(overlap.ledger["assessments"]) == 2
    assert sum(a["counted"] for a in overlap.ledger["assessments"]) == 1
    assert Merger().merge(base=base, candidates=[c, a]).ledger == overlap.ledger


def test_new_content_does_not_inherit_old_content_ranks(base):
    from thearc.models import Ranks

    base.skills["debug"].ranks = Ranks(helpful=4)
    a = edit(base, {"description": "New instructions"})
    result = Merger().merge(base=base, candidates=[a, branch(base)])
    assert result.agent.skills["debug"].ranks.helpful == 0
    assert result.ledger["baselines"][0]["ranks"]["helpful"] == 4


def test_merge_artifact_roundtrip_and_tampering(base, tmp_path):
    from thearc.resources import MergeResult

    result = Merger().merge(base=base, candidates=[edit(base, {"description": "New"})], output=tmp_path / "saved")
    restored = MergeResult.load(tmp_path / "saved")
    assert restored.agent == result.agent and restored.provenance == result.provenance
    with pytest.raises(TypeError):
        restored.provenance["source_epochs"]["new"] = "forged"
    path = tmp_path / "saved/merge.json"
    saved = json.loads(path.read_text())
    saved["merge"]["agent"]["name"] = "tampered"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="inconsistent"):
        MergeResult.load(path)
    with pytest.raises(FileExistsError):
        Merger().merge(base=base, candidates=[branch(base)], output=tmp_path / "saved")


def test_same_position_insertions_conflict(base):
    original = base.skills["debug"].instructions
    a = edit(base, {"instructions": original + "\nNew rule A.\n"})
    b = edit(base, {"instructions": original + "\nNew rule B.\n"})
    assert Merger().merge(base=base, candidates=[a, b]).unresolved


def test_conflicting_additions_and_identical_deletions(base):
    target = {"kind": "skill", "name": "new"}
    a = branch(base, target=target, operation="ADD", value={"description": "A"})
    b = branch(base, target=target, operation="ADD", value={"description": "B"})
    assert Merger().merge(base=base, candidates=[a, b]).unresolved
    a = branch(base, target={"kind": "context", "name": "AGENTS.md"}, operation="REMOVE")
    b = branch(base, target={"kind": "context", "name": "AGENTS.md"}, operation="REMOVE")
    result = Merger().merge(base=base, candidates=[a, b])
    assert not result.agent.context


@pytest.mark.parametrize("invalid", ["unknown", "duplicate", "missing"])
def test_resolver_must_cover_exact_conflicts(base, invalid):
    branches = conflicted(base)
    conflict = Merger().merge(base=base, candidates=branches).conflicts[0]
    result = report(conflict)
    if invalid == "unknown":
        result.resolutions[0].conflict_id = "other"
    elif invalid == "duplicate":
        result.resolutions.append(result.resolutions[0])
    else:
        result.resolutions.clear()
    with pytest.raises(ValueError, match="exactly once"):
        Merger().merge(base=base, candidates=branches, resolutions=result)


def test_selected_changes_do_not_cite_rejected_branch(base):
    branches = conflicted(base)
    conflict = Merger().merge(base=base, candidates=branches).conflicts[0]
    proposal = conflict.proposals[0]
    accepted = next(b for b in branches if b.epoch["epoch_id"] == proposal["candidate_id"])
    result = Merger().merge(base=base, candidates=branches, resolutions=report(conflict))
    expected = {r["id"] for r in accepted.epoch["reflections"]}
    assert set(result.changes[0].reflection_ids) == expected
    assert all(
        identity.startswith(proposal["candidate_id"] + ":") for identity in result.changes[0].raw["source_change_ids"]
    )


def test_unresolved_artifact_loads_but_exposes_no_agent(base, tmp_path):
    from thearc.resources import MergeResult

    Merger().merge(base=base, candidates=conflicted(base), output=tmp_path / "merge")
    loaded = MergeResult.load(tmp_path / "merge")
    assert loaded.unresolved
    with pytest.raises(ValueError, match="unresolved"):
        _ = loaded.agent


def test_combination_cannot_edit_executable_files(base):
    branches = conflicted(base)
    conflict = Merger().merge(base=base, candidates=branches).conflicts[0]
    change = {
        "id": "bad",
        "target": {"kind": "skill", "name": "debug"},
        "operation": "EDIT",
        "value": {"description": "Synthesis", "files": {"run.py": "modified"}},
        "reflection_ids": [branches[0].epoch["reflections"][0]["id"]],
        "rationale": "Unallowed",
    }
    with pytest.raises(ValueError, match="read-only"):
        Merger().merge(
            base=base, candidates=branches, resolutions=report(conflict, "combine", selected=[], changes=[change])
        )


@pytest.mark.parametrize("tamper", ["provenance", "ranks"])
def test_candidate_audit_cannot_disagree_with_content(base, tamper):
    from thearc.models import Ranks
    from thearc.resources.immutable import Record

    candidate = edit(base, {"description": "New"})
    epoch = thaw(candidate.epoch)
    agent = candidate.agent
    if tamper == "provenance":
        epoch["changes"][0]["before"]["description"] = "Invented baseline"
    else:
        agent.skills["debug"].ranks = Ranks(helpful=99)
        epoch["agent"] = agent.model_dump(mode="json")
    candidate = replace(
        candidate,
        _agent_json=agent.model_dump_json(),
        epoch=freeze(epoch),
        changes=tuple(Record(c) for c in epoch["changes"]),
    )
    with pytest.raises(ValueError):
        Merger().merge(base=base, candidates=[candidate])
