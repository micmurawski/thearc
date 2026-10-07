
import pytest
from pydantic import ValidationError

from thearc import AgentResource, ContextDocument, Hook, MetaAgent, Operation, ResourceTarget, Skill
from thearc.learning.curation.models import Curation


def agent():
    return MetaAgent(
        name="agent",
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
def test_curation_crud_targets(tmp_path, target, initial, edit):
    meta = agent()
    for operation, value in [(Operation.ADD, initial), (Operation.EDIT, edit), (Operation.REMOVE, None)]:
        curation = Curation(
            id=operation.value, target=target, operation=operation, value=value,
            reflection_ids=["reflection"], evidence_event_ids=["event"], rationale="Test evidence",
        )
        # The same payload must survive JSON transport between flow stages.
        curation = Curation.model_validate_json(curation.model_dump_json())
        meta.apply_change(curation.target, curation.operation, curation.value)
        if operation != Operation.REMOVE:
            actual = meta.read_target(target)
            assert actual == value if isinstance(value, str) else all(actual[k] == v for k, v in value.items())
        else:
            with pytest.raises(KeyError):
                meta.read_target(target)
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


def test_dictionary_inputs_are_validated_without_mutating_caller():
    source = {"graphify": {"description": "Example"}}
    meta = MetaAgent(name="agent", skills=source)
    assert source == {"graphify": {"description": "Example"}}
    assert meta.skills["graphify"].name == "graphify"
    assert list(meta.skills) == ["graphify"]
    assert MetaAgent.model_validate_json(meta.model_dump_json()) == meta
    with pytest.raises(ValidationError):
        MetaAgent(name="agent", skills={"graphify": Skill(name="other")})
    with pytest.raises(ValidationError):
        MetaAgent(name="agent", skills=[Skill(name="same"), Skill(name="same")])
