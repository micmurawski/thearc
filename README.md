# thearc ⚡

`thearc` is an extensible meta-framework for collecting and exploring episodic sessions from AI agents. Rather than prescribing one agent design, it provides the abstractions needed to turn agent traces into evidence for evaluating and improving an agentic system’s context files, skills, hooks, prompts, and MCP integrations.

Build agentic context-engineering pipelines on top of `thearc`: inspect sessions, extract reusable lessons and failures, curate versioned updates, evaluate their effect, and install the resulting capabilities into agent environments. The framework is designed to let these pipelines evolve as new artifacts and evaluation methods emerge, drawing direction from work on [agentic context engineering](https://arxiv.org/pdf/2510.04618), [meta-skill evolution](https://arxiv.org/pdf/2607.05297), [self-evolving agent skills](https://arxiv.org/pdf/2608.02636), [self-evolving agents](https://arxiv.org/html/2507.21046v4), and [meta context engineering](https://arxiv.org/pdf/2601.21557).

## Sessions, reflection, and curation

Index existing logs first:

```sh
thearc sessions --index sessions.sqlite index --source codex --source-id experiment --root /path/to/logs
thearc sessions --index sessions.sqlite list
```

Use the immutable resource interface:

```python
from thearc import MetaAgent
from thearc.resources import Curator, Reflector, SessionStore

agent = MetaAgent.from_workspace("my-agent")
with SessionStore("sessions.sqlite") as store:
    session = store.get(session_id)
trajectory = session.split_into_trajectories()[0]

reflector = Reflector(implementation="codex", model="YOUR_MODEL_ID")
reflection = reflector.handoff("Reflect on the task and suggest improvements.", trajectory, agent=agent)
curator = Curator(implementation="codex", model="YOUR_MODEL_ID")
result = curator.curate("Improve the guidance using these findings.", [reflection], agent=agent,
                        output="artifacts/curation")
adapted = result.agent
print(agent.diff(adapted))
```

For prompt reflection, compose evidence explicitly:
`reflector.reflect(PROMPT + "\n\n" + trajectory.to_markdown(), agent=agent)`.
Use `session.through(trajectory)` to include earlier conversation history in a handoff.
Reflection is the reflector's response; the continued session is a separate artifact.

Calls to reflection and curation invoke the selected runtime. The source history and
input MetaAgent remain unchanged. See the [resource guide](docs/guides/resources.md)
for schemas, validated resource assessments, activation support, and conversion limits.
The [ACE recipe](cookbook/resource_ace.py) demonstrates explicit evidence, assessments,
and a committed curation epoch. See [runtime setup](docs/reference/runtimes.md).

To combine parallel curation results, use [Merger](docs/guides/merging.md).

The old batch APIs and import aliases have been removed. See the
[migration guide](docs/guides/migration.md) for replacement calls.

### Install into multiple agents

The same `MetaAgent` can be installed for different agent harnesses, independently
of which backend produced it:

```python
for implementation in ("antigravity", "claude", "codex", "pi"):
    adapted.install(implementation)
```

These are separate project directories; use your project's path to install there.
Installation writes harness-specific configuration files, not agent executables,
and overwrites matching files by default. Review the diff before installing.

## Further reading

See the [glossary](GLOSSARY.md) for shared definitions of the main entities.

Browse the [documentation](docs/index.md), or preview the MkDocs site locally:

```sh
python -m pip install -e '.[docs]'
python -m mkdocs serve
```

For runnable evidence, reflection, and handoff examples, see the [cookbook](cookbook/README.md).
