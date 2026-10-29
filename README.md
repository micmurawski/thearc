# thearc ⚡

`thearc` is an extensible meta-framework for collecting and exploring episodic sessions from AI agents. Rather than prescribing one agent design, it provides the abstractions needed to turn agent traces into evidence for evaluating and improving an agentic system’s context files, skills, hooks, prompts, and MCP integrations.

Build agentic context-engineering pipelines on top of `thearc`: inspect sessions, extract reusable lessons and failures, curate versioned updates, evaluate their effect, and install the resulting capabilities into agent environments. The framework is designed to let these pipelines evolve as new artifacts and evaluation methods emerge, drawing direction from work on [agentic context engineering](https://arxiv.org/pdf/2510.04618), [meta-skill evolution](https://arxiv.org/pdf/2607.05297), [self-evolving agent skills](https://arxiv.org/pdf/2608.02636), [self-evolving agents](https://arxiv.org/html/2507.21046v4), and [meta context engineering](https://arxiv.org/pdf/2601.21557).

## The learning cycle

```text
sessions ─▶ reflection ─▶ ratings in the journal ─▶ curation ─▶ candidate ─▶ merge ─▶ new version
```

Index sessions from any supported harness:

```sh
thearc sessions --index sessions.sqlite add logs/codex-training
thearc sessions --index sessions.sqlite list
```

Reflect, record, and curate:

```python
from typing import Annotated
from pydantic import BaseModel
from thearc.curation import Curator
from thearc.reflection import Reflector
from thearc.resources import CategoricalScale, MetaAgent, Ref, ReflectionSchema
from thearc.sessions import Sessions

usefulness = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"], ordered=True)

class Rated(BaseModel):
    ref: Ref
    usefulness: Annotated[str, usefulness]

class Lesson(BaseModel):
    key_insight: str
    ratings: list[Rated]

agent = MetaAgent.from_installation("codex", "path/to/project", name="my-agent", version="1.0")
agent = agent.init_workspace("artifacts/my-agent")       # git-tracked, with a journal

with Sessions("sessions.sqlite") as sessions:
    session = sessions.get()                                  # the most recent session
reflection = Reflector("codex").handoff("Review the configuration against this session.",
                                        session, agent, ReflectionSchema(Lesson))
agent.add_reflection(reflection)                          # ratings → journal

curation = Curator("codex").curate("Address the reflections.", [reflection], agent, scales=[usefulness])
print(agent.diff(curation.candidate()))
```

Run pipelines in parallel worktrees (`agent.to_worktree(path)`) and combine their candidates with
`Merger("codex").merge(prompt, [candidate_1, candidate_2])`. Install the result into any
harness with `agent.install("claude", "path/to/project")`.

## Evolve a project with /evolve

```sh
thearc skills install self-evolve --project . --harness claude
```

Then type `/evolve` in the agent: thearc reflects on the project's recent Claude sessions, curates, and proposes a new
version of its skills and instructions. Review it with `/evolve diff`, ask the agent for amendments, and install it
with `/evolve apply`. The recipe is `evolve.py` in the evolve skill, with its prompts as Markdown files next to it.
See [the guide](docs/guides/evolve-skill.md).

## Further reading

- [Glossary](GLOSSARY.md) — every concept, kept in step with the code.
- [Documentation](docs/index.md) — guides and reference; preview it with
  `python -m pip install -e '.[docs]' && python -m mkdocs serve`.
- [Cookbook](cookbook/) — `live_reflect.py`, `live_ace.py`, `live_ace_with_merge.py`.
- [Parked problems](PARKED_PROBLEMS.md) — open questions.
