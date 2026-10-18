# Your first ACE run

This walk-through follows `cookbook/live_ace.py`: one round of reflection and curation on a
configuration installed in a project.

## 1. Import the configuration into a workspace

```python
from thearc.resources import MetaAgent

agent = MetaAgent.from_installation("codex", "tests/fastapi", name="graphify", version="0.9.68")
agent = agent.init_workspace("artifacts/graphify", branch="reflections")
```

The **workspace** is a git repository holding the configuration as files (`skills/…`,
`context/…`, `hooks.jsonc`) and the **journal** (`journal.jsonl`). Every heading carries a
marker with its section's ID; ratings stay in the journal (`agent_outline` shows them).

## 2. Index sessions and pick evidence

```python
from thearc.sessions import Sessions
from thearc.sessions.scanner import ShellCommand

with Sessions("artifacts/sessions.sqlite") as sessions:
    sessions.add("logs/codex-training")
    used = sorted({m.session_id for m in sessions.match(ShellCommand(contains="graphify"))})
    session = sessions.get(used[0])
```

## 3. Design the reflection

```python
from typing import Annotated
from pydantic import BaseModel, Field
from thearc.resources import CategoricalScale, Ref, ReflectionSchema

USEFULNESS = CategoricalScale("usefulness", {
    "harmful": "made the work worse", "neutral": "no visible effect", "helpful": "made the work better",
}, ordered=True)

class Rated(BaseModel):
    ref: Ref
    usefulness: Annotated[str, USEFULNESS]

class Lesson(BaseModel):
    error_identification: str = Field(description="What specifically went wrong?")
    key_insight: str = Field(description="What should be remembered?")
    ratings: list[Rated]

SCHEMA = ReflectionSchema(Lesson)
```

## 4. Reflect and record

```python
from thearc.reflection import Reflector

reflection = Reflector("codex").handoff("Review the configuration against this session.", session,
                                        agent, SCHEMA)
print(reflection.to_markdown())
agent.add_reflection(reflection)      # ratings appended to journal.jsonl
```

`handoff` installs a copy of the session into the reflector's harness and forks it, so the
reflector starts from that history.

## 5. Curate and review

```python
from thearc.curation import Curator

curation = Curator("codex").curate("Address the reflections.", [reflection], agent, scales=[USEFULNESS])
for commit in curation.commits:
    print(commit.sha[:10], commit.message.splitlines()[0], commit.changed)
print(agent.diff(curation.candidate()))
```

The curator's candidate lands on branch `curation/<id>`; your branch is left as it was.
To run several pipelines in parallel and merge them, see [merging](../guides/merging.md).
