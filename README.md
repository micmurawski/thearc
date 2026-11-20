# thearc ⚡

**Your coding agent's `CLAUDE.md`, `AGENTS.md`, and skills, improved from evidence of how it actually worked.**

thearc reads the sessions your agent ran in a project, has a reviewer agent rate each line of its instructions and
skills against what happened, and has a curator rewrite what hurt and keep what helped. Every rating and change lands
in a git-tracked journal, so each new version says why it changed, and you review it like a pull request before
installing it. It works across Claude Code, Codex, Pi, OpenCode, and Antigravity.

## Quickstart

```sh
python -m pip install thearc
cd your-project                                       # a git repository
thearc skills install self-evolve --harness claude    # or codex, pi, opencode
```

Commit what it installs (the skill, and the workspace as the submodule `.thearc/workspace`).

Work in the project as usual. After three sessions or more, type `/evolve` in the agent: it reflects on the last
week's sessions, curates, and proposes a new version of the project's instructions and skills. `/evolve diff` shows
it, the agent amends it when you ask, and `/evolve apply` installs it. A round is a short Python recipe (`evolve.py`
in the evolve skill, with its prompts beside it) that you can edit: see [the guide](docs/guides/evolve-skill.md).

No project at hand? [`examples/quickstart/run.sh`](examples/quickstart/) sets up a toy project whose `CLAUDE.md` has
a wrong instruction, runs three Claude Code sessions in it, and runs a round that finds and fixes it. The
[example evolution report](https://micmurawski.github.io/thearc/examples/report.html) shows four releases, parallel
candidates, merges, and section histories from a Graphify evolution workspace.

## Supported agents

| Agent | Read its sessions | Install configurations (project / global) | `/evolve` in it | Reflect, curate, merge |
| --- | --- | --- | --- | --- |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | yes | yes / yes | yes | yes |
| [Codex](https://github.com/openai/codex) | yes | yes / yes | yes | yes |
| [Pi](https://github.com/badlogic/pi-mono) | yes | yes / yes | yes | yes |
| [OpenCode](https://opencode.ai) | yes | yes / yes | yes | yes |
| [Antigravity](https://antigravity.google) | yes | yes / no | no | no |

Configurations cover context files (`CLAUDE.md`, `AGENTS.md`, …), skills, hooks, and MCP servers, as far as each
agent supports them (Pi has no hooks or MCP servers). Details per harness: [runtime support](docs/reference/runtimes.md).

## The framework

`thearc` is a framework for context engineering: it turns your agents' sessions into evidence, and that evidence into
versioned changes to their context files, skills, hooks, prompts, and MCP servers. It doesn't prescribe an agent
design; it gives you the pieces — a session index and query language, reflectors, curators, mergers, and a
git-tracked journal — to build your own improvement pipelines.

Build agentic context-engineering pipelines on top of `thearc`: inspect sessions, extract reusable lessons and failures, curate versioned updates, evaluate their effect, and install the resulting capabilities into agent environments. The framework is designed to let these pipelines evolve as new artifacts and evaluation methods emerge, drawing direction from work on [agentic context engineering](https://arxiv.org/pdf/2510.04618), [meta-skill evolution](https://arxiv.org/pdf/2607.05297), [self-evolving agent skills](https://arxiv.org/pdf/2608.02636), [self-evolving agents](https://arxiv.org/html/2507.21046v4), and [meta context engineering](https://arxiv.org/pdf/2601.21557).

## The learning cycle

```text
sessions ─▶ reflection ─▶ ratings in the journal ─▶ curation ─▶ candidate ─▶ merge ─▶ new version
```

Index sessions from any supported harness:

```sh
thearc sessions --index sessions.sqlite sync logs/codex-training
thearc sessions --index sessions.sqlite query 'select:session'
```

Reflect, record, and curate:

```python
from typing import Annotated
from pydantic import BaseModel
from thearc.curation import Curator
from thearc.reflection import Reflector
from thearc.resources import CategoricalScale, AgentConfig, Ref
from thearc.sessions import Sessions

usefulness = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"], ordered=True)

class Rated(BaseModel):
    ref: Ref
    usefulness: Annotated[str, usefulness]

class Lesson(BaseModel):
    key_insight: str
    ratings: list[Rated]

agent = AgentConfig.from_installation("codex", "path/to/project", name="my-agent", version="1.0")
agent = agent.init_workspace("artifacts/my-agent")       # git-tracked, with a journal

with Sessions("sessions.sqlite") as sessions:
    session = sessions.get()                                  # the most recent session
reflection = Reflector("codex").handoff("Review the configuration against this session.",
                                        session, agent, Lesson)
agent.add_reflection(reflection)                          # ratings → journal

curation = Curator("codex").curate("Address the reflections.", [reflection], agent, scales=[usefulness])
print(agent.diff(curation.candidate()))
```

Run pipelines in parallel worktrees (`agent.to_worktree(path)`) and combine their candidates with
`Merger("codex").merge(prompt, [candidate_1, candidate_2])`. Install the result into any
harness with `agent.install("claude", "path/to/project")`.

## Further reading

- [Concepts](docs/concepts.md) — every concept, kept in step with the code.
- [Documentation](docs/index.md) — guides and reference; preview it with
  `python -m pip install -e '.[docs]' && python -m mkdocs serve`.
- [Quickstart example](examples/quickstart/) — a toy project and one round, end to end.
- [Cookbook](cookbook/) — `live_reflect.py`, `live_ace.py`, `live_ace_with_merge.py`.
- [Benchmarking](benchmarking/README.md) — notes on measuring whether an evolved agent does better.

## Contributing and license

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md), and report vulnerabilities as
[SECURITY.md](SECURITY.md) describes. thearc is licensed under the [Apache License 2.0](LICENSE).
