# Generate reflections

A **reflection** is a reflector agent's review of a configuration against evidence of how an
agent behaved. The reflector reads the configuration's files — without any ratings or IDs — and
answers in the shape of a **reflection schema** you design.

## The schema

thearc needs only two kinds of fields, found by type:

- `Ref` — a file reference to configuration lines, `path#Lx` or `path#Lx-Ly`. Wherever it
  appears, it must resolve.
- A rating — `Annotated[str, scale]` (or `scale.field()`) — under any field name.

**A rating always comes with a ref; a ref can stand alone; neither is required.** Everything else
is yours.

```python
from typing import Annotated
from pydantic import BaseModel, Field
from thearc.resources import CategoricalScale, NumericScale, Ref, ReflectionSchema

usefulness = CategoricalScale("usefulness", {"harmful": "…", "neutral": "…", "helpful": "…"}, ordered=True)

class Rated(BaseModel):
    ref: Ref
    usefulness: Annotated[str, usefulness]

class Lesson(BaseModel):
    """One lesson from a trajectory."""
    root_cause_analysis: str = Field(description="Why did this error occur?")
    key_insight: str = Field(description="What should be remembered?")
    ratings: list[Rated]
    see_also: list[Ref] = []

schema = ReflectionSchema(Lesson)
```

Scales are `CategoricalScale` (labels, optionally ordered from worst to best, each with a meaning),
`LabelScale` (open-ended tags), and `NumericScale` (bounded numbers). Descriptions of fields, labels,
and the model reach the reflector through the schema.

The evolution report draws a `CategoricalScale` from two more settings, which reflectors never see.
`values` gives each label a number that rises with the label order: labels below zero are bad, a label
at zero is neither, labels above zero are good, and the report draws the scale diverging around zero.
Values that are all zero or above make a sequential scale, drawn from its first label up. `colors`
gives labels their own colour; the rest get one from their value.

```python
usefulness = CategoricalScale(
    "usefulness", ["catastrophic", "harmful", "neutral", "useful"],
    values={"catastrophic": -2, "harmful": -1, "neutral": 0, "useful": 1},
    colors={"catastrophic": "#7a0019"},
)
importance = CategoricalScale(
    "importance", ["meaningless", "significant", "important", "very important", "critical"],
    values={"meaningless": 0, "significant": 1, "important": 2, "very important": 3, "critical": 4},
)
```

An ordered scale without values is diverging, its labels evenly spaced around zero. `ReflectionSchema(scale, Metadata)` is shorthand
for a list of `{ref, rating, …}` items.

## Running a reflector

```python
from thearc.reflection import Reflector

reflector = Reflector("codex")                                       # or "claude", "pi"
reflection = reflector.reflect(PROMPT, agent, schema, evidence=text)  # evidence in the prompt
reflection = reflector.handoff(PROMPT, session, agent, schema)       # evidence as forked history
```

The reflector hands its answer in through a `submit_reflection` tool connected for that run. The
tool validates the whole output and resolves every ref; on any problem it returns the reasons by
location (`/ratings/1/ref: …`) and the reflector fixes them in its own loop.

A session record does not always show which configuration the agent ran with: OpenCode's leaves
out its system prompt, so the project instructions and skills it loaded are invisible, and a
reflector rightly refuses to credit or blame files it cannot see were loaded. `handoff` tells the
reflector what a harness's record leaves out, and `provenance` adds what you know yourself:

```python
reflector.handoff(PROMPT, session, agent, schema,
                  provenance=f"thearc had installed {agent.name} {agent.version} for opencode in this project.")
```

Both reach the reflector under "How the session ran (not in its record)"; `provenance` is also
kept in `reflection.evidence`.

## Using the result

```python
reflection.output          # the answer, exactly as the schema declares it
reflection.items           # the ratings found in it: ref, value, scale, location
print(reflection.to_markdown())
reflection.dump_items_to_json("reflection.json")
agent.add_reflection(reflection)   # Rated entries, one per section a ref covers; appended to journal.jsonl
```

`add_reflection` refuses a reflection of another revision, and adding the same one twice records
nothing new.
