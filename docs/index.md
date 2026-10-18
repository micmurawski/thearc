# thearc

`thearc` improves an AI agent's configuration — its context files, skills, hooks, MCP servers,
and extensions — from evidence of how the agent actually behaved. It is harness-independent:
it reads sessions from Claude Code, Codex, Pi, OpenCode, and Antigravity, and installs
configurations back into them.

One learning cycle (ACE — agentic context engineering):

```text
sessions ─▶ reflection ─▶ ratings in the journal ─▶ curation ─▶ candidate ─▶ merge ─▶ new version
```

- **Sessions** are indexed from each harness's transcripts and split into trajectories.
- **Reflection**: a reflector agent reviews the configuration against a session — like a code
  review where the evidence is what the agent did — and returns output in a schema you design,
  with ratings on the lines it judged.
- **The journal** records those ratings per section, together with every change to the
  configuration and its reason, in a git-tracked workspace.
- **Curation**: a curator agent edits the configuration in response to the reflections, one
  commit per change, with the reason as the commit message.
- **Merging**: candidates curated in parallel are combined section by section by a merger agent.

```python
from thearc.resources import MetaAgent
from thearc.reflection import Reflector
from thearc.curation import Curator

agent = MetaAgent.from_installation("codex", "path/to/project", name="graphify", version="1.0")
agent = agent.init_workspace("artifacts/graphify")      # git-tracked, with a journal

reflection = Reflector("codex").handoff(PROMPT, session, agent, SCHEMA)
agent.add_reflection(reflection)                         # ratings → journal

curation = Curator("codex").curate(CURATION_PROMPT, [reflection], agent, scales=[USEFULNESS])
print(agent.diff(curation.candidate()))
```

Start with [installation](start/installation.md) and the [first ACE run](start/first-ace.md). The
[glossary](https://github.com/micmurawski/thearc/blob/main/GLOSSARY.md) defines every concept.
