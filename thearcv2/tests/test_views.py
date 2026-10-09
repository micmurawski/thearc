import pytest

from thearcv2.resources import (
    CategoricalScale,
    LabelScale,
    ReflectionSchema,
    ResourceTarget,
    agent_outline,
    annotated_files,
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


def test_headings_carry_summaries_and_lines_stay_put(rated):
    plain = rated.files().files[SKILL]
    annotated = annotated_files(rated, usefulness)[SKILL]
    assert len(annotated.splitlines()) == len(plain.splitlines())
    assert annotated.splitlines()[10] == "## Examples (harmful: 0, neutral: 0, helpful: 1)"
    assert annotated.splitlines()[14] == "## Limits"


def test_several_scales_are_named(rated):
    line = annotated_files(rated, [usefulness, LabelScale("tags")])[SKILL].splitlines()[10]
    assert line == "## Examples usefulness: (harmful: 0, neutral: 0, helpful: 1) tags: (outdated: 1)"


def test_view_does_not_change_the_agent(rated):
    revision = rated.revision
    annotated_files(rated, usefulness)
    assert rated.revision == revision and "helpful: 1" not in rated.files().files[SKILL]


def test_outline_covers_whole_resources(rated):
    outline = agent_outline(rated, usefulness)
    assert "- hook: guard `hooks.json#L2-L4` (harmful: 1, neutral: 0, helpful: 0)" in outline
    assert "    - Examples `skills/retry/SKILL.md#L11-L14` (harmful: 0, neutral: 0, helpful: 1)" in outline
    assert "  - Retry `skills/retry/SKILL.md#L7-L17`\n" in outline


def test_summaries_follow_renamed_sections(rated, human):
    edited = rated.model_copy(deep=True)
    edited.skills["retry"].instructions = edited.skills["retry"].instructions.replace("## Examples", "## Samples")
    edited.journal.record(*detect_changes(rated, edited, **human))
    assert "## Samples (harmful: 0, neutral: 0, helpful: 1)" in annotated_files(edited, usefulness)[SKILL]


def test_section_ids_with_and_without_ratings(rated):
    examples = ResourceTarget(kind="skill", name="retry", section="Retry > Examples")
    line = annotated_files(rated, ids=True)[SKILL].splitlines()[10]
    assert line == f"## Examples [{examples.id}]"
    line = annotated_files(rated, usefulness, ids=True)[SKILL].splitlines()[10]
    assert line == f"## Examples [{examples.id}] (harmful: 0, neutral: 0, helpful: 1)"
    assert annotated_files(rated) == rated.files().files
    assert f"[{examples.id}]" in agent_outline(rated, ids=True)
