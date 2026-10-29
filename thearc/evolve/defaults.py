"""The default reflection schema of a round: lessons from a session, with the configuration lines rated on usefulness.

The recipe (``evolve.py`` in the evolve skill) imports these; a recipe may declare its own instead.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from thearc.resources import CategoricalScale, Ref, ReflectionSchema

USEFULNESS = CategoricalScale(
    "usefulness",
    {
        "harmful": "the cited lines made the agent's work worse",
        "neutral": "the cited lines were evidently followed but made no difference",
        "helpful": "the cited lines made the agent's work better",
    },
    values={"harmful": -1, "neutral": 0, "helpful": 1},  # Order, and which side of neutral: the report diverges.
    description="How the cited configuration lines affected the session.",
)


class Rated(BaseModel):
    """One judgment of cited configuration lines."""

    ref: Ref
    usefulness: Annotated[str, USEFULNESS]


class Lesson(BaseModel):
    """What the session teaches about the configuration."""

    reasoning: str = Field(min_length=1, description="Your chain of thought: detailed analysis of the evidence.")
    error_identification: str = Field(min_length=1, description="What specifically went wrong in the session?")
    root_cause_analysis: str = Field(
        min_length=1, description="Why did this error occur? What concept or instruction was misunderstood?"
    )
    correct_approach: str = Field(min_length=1, description="What should the agent have done instead?")
    key_insight: str = Field(
        min_length=1, description="What strategy or principle should be remembered to avoid this error?"
    )
    ratings: list[Rated] = Field(description="The configuration lines that shaped the behaviour, each rated.")


SCHEMA = ReflectionSchema(Lesson)
