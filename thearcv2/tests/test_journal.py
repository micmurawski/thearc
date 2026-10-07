import pytest
from pydantic import BaseModel

from thearcv2.resources import (
    CategoricalScale,
    LabelScale,
    ReflectionSchema,
    ResourceTarget,
    detect_changes,
    since_last_modification,
)

usefulness = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"], ordered=True)
EXAMPLES = ResourceTarget(kind="skill", name="retry", section="Retry > Examples")
LIMITS = ResourceTarget(kind="skill", name="retry", section="Retry > Limits")


def rate(agent, extra, *items, scale=usefulness, metadata=None):
    result = ReflectionSchema(scale, metadata).record(list(items), agent, **extra)
    agent.journal.record(*result.entries)
    return result


class Insight(BaseModel):
    insight: str
    root_cause_analysis: str = ""


def test_reflection_items_become_rated_entries(agent, human):
    result = rate(agent, human, {"ref": "skills/retry/SKILL.md#L13-L17", "rating": "harmful",
                                 "insight": "loops", "root_cause_analysis": "no backoff"}, metadata=Insight)
    assert [entry.target for entry in result.entries] == [EXAMPLES, LIMITS]
    first, second = result.entries
    assert first.item_id == second.item_id
    assert first.ref == "skills/retry/SKILL.md#L13-L17"
    assert first.revision == agent.revision and first.extra == human
    assert first.metadata == {"insight": "loops", "root_cause_analysis": "no backoff"}


def test_invalid_items_are_rejected_not_recorded(agent, human):
    result = rate(agent, human,
                  {"ref": "skills/retry/SKILL.md#L13", "rating": "great", "insight": "x"},
                  {"ref": "skills/retry/SKILL.md#L99", "rating": "helpful", "insight": "x"},
                  {"ref": "skills/retry/SKILL.md#L13", "rating": "helpful"},
                  {"ref": "skills/retry/SKILL.md#L13", "rating": "helpful", "insight": "ok"},
                  metadata=Insight)
    assert [rejection.index for rejection in result.rejected] == [0, 1, 2]
    assert len(result.entries) == len(agent.journal) == 1


def test_metadata_is_free_form_without_a_model(agent, human):
    result = rate(agent, human, {"ref": "skills/retry/SKILL.md#L13", "rating": "helpful", "anything": [1, 2]})
    assert result.entries[0].metadata == {"anything": [1, 2]}


def test_json_schema_combines_core_and_metadata():
    schema = ReflectionSchema(usefulness, Insight).json_schema()
    item = schema["items"]
    assert item["required"] == ["ref", "rating", "insight"]
    assert item["properties"]["rating"]["enum"] == ["harmful", "neutral", "helpful"]
    assert item["additionalProperties"] is False
    with pytest.raises(ValueError):
        ReflectionSchema(usefulness, type("Clash", (BaseModel,), {"__annotations__": {"ref": str}}))


def test_summaries_per_scale(agent, human):
    rate(agent, human, {"ref": "skills/retry/SKILL.md#L13", "rating": "helpful"},
         {"ref": "skills/retry/SKILL.md#L13-L17", "rating": "harmful"})
    rate(agent, human, {"ref": "skills/retry/SKILL.md#L13", "rating": "Outdated"}, scale=LabelScale("tags"))
    summaries = agent.journal.summaries(usefulness)
    assert summaries[EXAMPLES].counts == {"harmful": 1, "neutral": 0, "helpful": 1}
    assert summaries[LIMITS].counts == {"harmful": 1, "neutral": 0, "helpful": 0}
    assert agent.journal.summaries(LabelScale("tags"))[EXAMPLES].counts == {"outdated": 1}


def test_initial_import_adds_every_target(agent, human):
    entries = detect_changes(None, agent, **human)
    assert {entry.kind for entry in entries} == {"added"}
    assert {entry.target for entry in entries} == set(agent.files().targets)


def test_modified_section_only(agent, human):
    edited = agent.model_copy(deep=True)
    edited.skills["retry"].instructions = edited.skills["retry"].instructions.replace("max 3", "max 5")
    entries = detect_changes(agent, edited, **human, commit="abc123")
    assert [(entry.kind, entry.target) for entry in entries] == [("modified", LIMITS)]
    assert entries[0].commit == "abc123" and entries[0].revision == edited.revision


def test_renamed_section_is_moved_and_keeps_its_ratings(agent, human):
    rate(agent, human, {"ref": "skills/retry/SKILL.md#L17", "rating": "helpful"})
    edited = agent.model_copy(deep=True)
    edited.skills["retry"].instructions = edited.skills["retry"].instructions.replace("## Limits", "## Bounds")
    entries = detect_changes(agent, edited, **human)
    bounds = LIMITS.model_copy(update={"section": "Retry > Bounds"})
    assert [(entry.kind, entry.target) for entry in entries] == [("moved", bounds)]
    agent.journal.record(*entries)
    assert agent.journal.summaries(usefulness)[bounds].counts["helpful"] == 1
    assert [entry.kind for entry in agent.journal.history(bounds)] == ["rated", "moved"]


def test_renamed_skill_moves_its_sections_and_files(agent, human):
    edited = agent.model_copy(deep=True)
    skill = edited.skills.pop("retry")
    edited.skills = {**edited.skills, "backoff": skill.model_copy(update={"name": "backoff"})}
    entries = detect_changes(agent, edited, **human)
    assert {entry.kind for entry in entries} == {"moved"}
    assert {(entry.previous.name, entry.target.name) for entry in entries} == {
        ("retry", "backoff"), ("retry/notes.md", "backoff/notes.md"), ("retry/run.sh", "backoff/run.sh")}


def test_ambiguous_move_is_removed_plus_added(agent, human):
    edited = agent.model_copy(deep=True)
    edited.skills["retry"].instructions = edited.skills["retry"].instructions.replace(
        "## Limits\n\nmax 3", "## Bounds\n\nmax 4")
    kinds = sorted(entry.kind for entry in detect_changes(agent, edited, **human))
    assert kinds == ["added", "removed"]


def test_removed_targets_have_no_summary(agent, human):
    rate(agent, human, {"ref": "skills/retry/SKILL.md#L17", "rating": "helpful"})
    edited = agent.model_copy(deep=True)
    edited.skills["retry"].instructions = "# Retry\n\nUse retries.\n"
    agent.journal.record(*detect_changes(agent, edited, **human))
    assert LIMITS not in agent.journal.summaries(usefulness)


def test_policy_can_drop_ratings_given_before_an_edit(agent, human):
    rate(agent, human, {"ref": "skills/retry/SKILL.md#L17", "rating": "harmful"})
    edited = agent.model_copy(deep=True)
    edited.skills["retry"].instructions = edited.skills["retry"].instructions.replace("max 3", "max 5")
    edited.journal.record(*detect_changes(agent, edited, **human))
    rate(edited, human, {"ref": "skills/retry/SKILL.md#L17", "rating": "helpful"})
    assert edited.journal.summaries(usefulness)[LIMITS].counts == {"harmful": 1, "neutral": 0, "helpful": 1}
    recent = edited.journal.summaries(usefulness, since_last_modification)[LIMITS]
    assert recent.counts == {"harmful": 0, "neutral": 0, "helpful": 1}


def test_journal_is_append_only(agent, human):
    result = rate(agent, human, {"ref": "skills/retry/SKILL.md#L17", "rating": "helpful"})
    with pytest.raises(ValueError, match="duplicate"):
        agent.journal.record(*result.entries)


def test_extra_must_be_json(agent):
    with pytest.raises(ValueError, match="JSON"):
        detect_changes(None, agent, when=object())
