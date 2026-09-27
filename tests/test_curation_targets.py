import hashlib
import json
from functools import partial

import pytest
from pydantic import ValidationError

from thearc import AgentResource, ContextDocument, Hook, MetaAgent, Operation, ResourceTarget, Skill
from thearc.learning import ChangeJournal, Curation, apply_curations
from thearc.models import Ranks


def agent():
    return MetaAgent(
        skills=[Skill(name="graphify", files={"references/query.md": "# Query\n\nOriginal."})],
        context=[ContextDocument(filename="AGENTS.md", content="# Rules\n\nOriginal.")],
        hooks=[Hook(name="guard", command="echo guard")],
        resources=[AgentResource(location="rules", path="existing.md", content="# Existing\n\nOriginal.")],
    )


@pytest.mark.parametrize(("target", "initial", "edit"), [
    (ResourceTarget(kind="skill", name="new"), {"description": "Initial"}, {"description": "Changed"}),
    (ResourceTarget(kind="hook", name="new"), {"command": "echo initial"}, {"command": "echo changed"}),
    (ResourceTarget(kind="mcp", name="new"), {"command": "initial"}, {"args": ["changed"]}),
    (ResourceTarget(kind="rule", name="new.md"), {"content": "Initial"}, {"content": "Changed"}),
    (ResourceTarget(kind="workflow", name="new.md"), {"content": "Initial"}, {"content": "Changed"}),
    (ResourceTarget(kind="command", name="new.md"), {"content": "Initial"}, {"content": "Changed"}),
    (ResourceTarget(kind="rule", name="existing.md", section="New"), {"content": "Initial"}, {"content": "Changed"}),
    (ResourceTarget(kind="context", name="NEW.md"), {"content": "Initial"}, {"content": "Changed"}),
    (ResourceTarget(kind="skill_file", name="graphify/references/new.md"), "Initial", "Changed"),
    (ResourceTarget(kind="context", name="AGENTS.md", section="New"), {"content": "Initial"}, {"content": "Changed"}),
    (ResourceTarget(kind="skill", name="graphify", section="New"), {"content": "Initial"}, {"content": "Changed"}),
    (ResourceTarget(kind="skill_file", name="graphify/references/query.md", section="New"),
     {"content": "Initial"}, {"content": "Changed"}),
])
def test_curation_crud_targets_and_journal(tmp_path, target, initial, edit):
    meta = agent()
    journal = ChangeJournal(tmp_path / "changes.jsonl")
    for operation, value in [(Operation.ADD, initial), (Operation.EDIT, edit), (Operation.REMOVE, None)]:
        curation = Curation(
            id=operation.value, target=target, operation=operation, value=value,
            reflection_ids=["reflection"], evidence_event_ids=["event"], rationale="Test evidence",
        )
        # The same payload must survive JSON transport between flow stages.
        curation = Curation.model_validate_json(curation.model_dump_json())
        result = apply_curations(meta, [curation], partial(journal.record, "test"))
        meta = result.agent
        assert result.applied_curation_ids == [operation.value]
        assert result.rejected_curation_ids == []
        if operation != Operation.REMOVE:
            actual = meta.read_target(target)
            assert actual == value if isinstance(value, str) else all(actual[k] == v for k, v in value.items())
        else:
            with pytest.raises(KeyError):
                meta.read_target(target)
    entries = journal.entries()
    assert [entry.operation for entry in entries] == list(Operation)
    assert all(entry.target == target for entry in entries)
    assert all(entry.reason.evidence_event_ids == ["event"] for entry in entries)
    assert entries[0].after_sha256 == entries[1].before_sha256
    assert entries[1].after_sha256 == entries[2].before_sha256
    assert entries[0].before_sha256 == hashlib.sha256(json.dumps(None).encode()).hexdigest()
    # An unrelated bundled reference remains present.
    assert "Original." in meta.skills["graphify"].files["references/query.md"]


def test_invalid_changes_leave_agent_unchanged(tmp_path):
    meta = agent()
    before = meta.model_dump()
    with pytest.raises(ValueError):
        meta.apply_change(ResourceTarget(kind="skill", name="graphify"), Operation.ADD, {})
    with pytest.raises(KeyError):
        meta.apply_change(ResourceTarget(kind="hook", name="missing"), Operation.EDIT, {"command": "echo x"})
    with pytest.raises(ValueError):
        meta.apply_change(ResourceTarget(kind="skill", name="graphify"), Operation.EDIT, {"name": "other"})
    with pytest.raises(ValidationError):
        ResourceTarget(kind="hook", name="guard", section="invalid")
    with pytest.raises(ValidationError):
        Curation(id="bad", target={"kind": "skill", "name": "x"}, operation="UPDATE", reflection_ids=[])
    assert meta.model_dump() == before


def test_metaagent_json_roundtrip_and_rank_free_sections():
    meta = agent()
    target = ResourceTarget(kind="skill_file", name="graphify/references/query.md", section="Query")
    meta.apply_rank_delta(target, Ranks(helpful=2))
    restored = MetaAgent.model_validate_json(meta.model_dump_json())
    assert restored == meta
    assert restored.rank_for(target).helpful == 2
    assert restored.without_ranks().rank_for(target).helpful == 0
    assert restored.rank_for(target).helpful == 2


def test_dictionary_inputs_are_validated_without_mutating_caller():
    source = {"graphify": {"description": "Example"}}
    meta = MetaAgent(skills=source)
    assert source == {"graphify": {"description": "Example"}}
    assert meta.skills["graphify"].name == "graphify"
    assert list(meta.skills) == ["graphify"]
    assert MetaAgent.model_validate_json(meta.model_dump_json()) == meta
    with pytest.raises(ValidationError):
        MetaAgent(skills={"graphify": Skill(name="other")})
    with pytest.raises(ValidationError):
        MetaAgent(skills=[Skill(name="same"), Skill(name="same")])
