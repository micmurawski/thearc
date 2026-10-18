import pytest

from thearc.resources import (
    CategoricalScale,
    LabelScale,
    ReflectionSchema,
    ResourceTarget,
    agent_outline,
    detect_changes,
)

usefulness = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"], ordered=True)
SKILL = "skills/retry/SKILL.md"


@pytest.fixture
def rated(agent, human):
    for scale, rating in ((usefulness, "helpful"), (LabelScale("tags"), "outdated")):
        result = ReflectionSchema(scale).record([{"ref": f"{SKILL}#L13", "rating": rating}], agent, **human)
        agent.journal.record(*result.entries)
    result = ReflectionSchema(usefulness).record([{"ref": "hooks.json#L3", "rating": "harmful"}], agent, **human)
    agent.journal.record(*result.entries)
    return agent


def test_outline_does_not_change_the_agent(rated):
    revision = rated.revision
    agent_outline(rated, usefulness, ids=True)
    assert rated.revision == revision and "helpful: 1" not in rated.files().files[SKILL]


def test_several_scales_are_named(rated):
    outline = agent_outline(rated, [usefulness, LabelScale("tags")])
    assert ("- Examples `skills/retry/SKILL.md#L11-L14` usefulness: (harmful: 0, neutral: 0, helpful: 1) "
            "tags: (outdated: 1)") in outline


def test_outline_covers_whole_resources(rated):
    outline = agent_outline(rated, usefulness)
    assert "- hook: guard `hooks.json#L2-L4` (harmful: 1, neutral: 0, helpful: 0)" in outline
    assert "    - Examples `skills/retry/SKILL.md#L11-L14` (harmful: 0, neutral: 0, helpful: 1)" in outline
    assert "  - Retry `skills/retry/SKILL.md#L7-L17`\n" in outline


def test_summaries_follow_renamed_sections(rated, human):
    edited = rated.model_copy(deep=True)
    edited.skills["retry"].instructions = edited.skills["retry"].instructions.replace("## Examples", "## Samples")
    edited.journal.record(*detect_changes(rated, edited, **human))
    assert "- Samples `skills/retry/SKILL.md#L11-L14` (harmful: 0, neutral: 0, helpful: 1)" in agent_outline(
        edited, usefulness)


def test_outline_ids_are_lineage_ids(rated):
    examples = ResourceTarget(kind="skill", name="retry", section="Retry > Examples")
    outline = agent_outline(rated, usefulness, ids=True)
    assert f"- Examples `skills/retry/SKILL.md#L11-L14` [{examples.id}] (harmful: 0, neutral: 0, helpful: 1)" in outline
