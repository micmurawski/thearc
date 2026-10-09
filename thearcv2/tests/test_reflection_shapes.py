"""Reflection schemas of any shape: ratings are found by type, each next to the ref it is about."""

import json

import pytest
from pydantic import BaseModel, Field

from thearcv2.curation import Curator
from thearcv2.reflection import Reflection, Reflector
from thearcv2.resources import CategoricalScale, NumericScale, Ref, ReflectionSchema

from .test_curation import ScriptedCurator
from .test_reflection import Scripted

usefulness = CategoricalScale("usefulness", {"helpful": "made the work better", "neutral": "no effect",
                                             "harmful": "made the work worse"})
confidence = NumericScale("confidence", minimum=0, maximum=1)


class Tagged(BaseModel):
    ref: Ref
    usefulness: usefulness.field()


class Lesson(BaseModel):
    """One lesson from a trajectory."""

    reasoning: str = Field(description="Your chain of thought.")
    error_identification: str = Field(description="What specifically went wrong?")
    key_insight: str = Field(description="What should be remembered?")
    ratings: list[Tagged]
    see_also: list[Ref] = []


LESSON = ReflectionSchema(Lesson)
ANSWER = {
    "reasoning": "The agent reran a SyntaxError ten times.",
    "error_identification": "It treated a permanent error as transient.",
    "key_insight": "Fix permanent errors; retry only transient ones.",
    "ratings": [{"ref": "skills/retry/SKILL.md#L17", "usefulness": "harmful"},
                {"ref": "context/AGENTS.md#L3", "usefulness": "helpful"}],
    "see_also": ["skills/retry/SKILL.md#L9"],
}


def test_the_schema_finds_ratings_and_refs_by_type_and_describes_everything():
    schema = LESSON.json_schema()
    assert schema["description"] == "One lesson from a trajectory." and "$defs" not in schema
    rated = schema["properties"]["ratings"]["items"]
    assert rated["required"] == ["ref", "usefulness"] and "made the work worse" in rated["properties"][
        "usefulness"]["description"]
    output, items, refs = LESSON.validate(ANSWER)
    assert output == {**ANSWER}
    assert [(i.path, i.scale, i.rating, str(i.ref)) for i in items] == [
        ("/ratings/0", "usefulness", "harmful", "skills/retry/SKILL.md#L17"),
        ("/ratings/1", "usefulness", "helpful", "context/AGENTS.md#L3"),
    ]
    assert [where for where, _ in refs] == ["/ratings/0/ref", "/ratings/1/ref", "/see_also/0"]


def test_a_rating_needs_exactly_one_required_ref_beside_it():
    class Loose(BaseModel):
        usefulness: usefulness.field()

    class Optional(BaseModel):
        ref: Ref | None = None
        usefulness: usefulness.field()

    class Two(BaseModel):
        ref: Ref
        other: Ref
        usefulness: usefulness.field()

    for model in (Loose, Optional, Two):
        with pytest.raises(ValueError, match="exactly one required Ref"):
            ReflectionSchema(model)


def test_several_scales_and_an_optional_rating():
    class Judged(BaseModel):
        ref: Ref
        usefulness: usefulness.field()
        sure: confidence.field() | None = None
        why: str

    schema = ReflectionSchema(list[Judged])
    _, items, _ = schema.validate([{"ref": "a.md#L1", "usefulness": "neutral", "sure": 0.5, "why": "w"},
                                   {"ref": "a.md#L2", "usefulness": "helpful", "why": "v"}])
    assert [(i.path, i.scale, i.rating, i.metadata) for i in items] == [
        ("/0", "usefulness", "neutral", {"why": "w"}), ("/0", "confidence", 0.5, {"why": "w"}),
        ("/1", "usefulness", "helpful", {"why": "v"})]
    assert sorted(schema.scales) == ["confidence", "usefulness"]
    with pytest.raises(ValueError, match="/0/sure"):
        schema.validate([{"ref": "a.md#L1", "usefulness": "neutral", "sure": 2, "why": "w"}])


def test_a_lesson_is_submitted_whole_recorded_and_curated(agent, tmp_path):
    workspace = agent.init_workspace(tmp_path / "ws")
    wrong = {**ANSWER, "see_also": ["skills/retry/SKILL.md#L99"]}
    runtime = Scripted([wrong, ANSWER])
    reflection = Reflector(runtime=runtime).reflect("Review.", workspace, LESSON)
    assert "/see_also/0: " in runtime.results[0]["content"][0]["text"]  # Loose refs are checked too.
    assert reflection.output == ANSWER and len(reflection) == 2 and reflection.attempts == 2
    entries = workspace.add_reflection(reflection)
    assert {(e.item_id.split("#")[1], e.value) for e in entries} == {("/ratings/0", "harmful"),
                                                                     ("/ratings/1", "helpful")}
    assert all(e.metadata == {} for e in entries)  # The lesson's text is saved once, in the reflection.
    saved = json.loads(json.dumps(reflection.to_dict()))
    assert saved["output"] == ANSWER and Reflection.from_dict(saved, LESSON) == reflection

    curator = ScriptedCurator(lambda root, tool: tool("finish", summary="Kept."))
    Curator(runtime=curator).curate("Act on the lesson.", [reflection], workspace)
    prompt = curator.sent[0]
    assert "#### Key Insight\n\nFix permanent errors; retry only transient ones." in prompt
    assert f"`{reflection.id}#/ratings/0` usefulness **harmful**" in prompt
