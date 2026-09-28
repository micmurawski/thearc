# ACE in a few lines

**Give it your configuration and a past session. Get back an adapted configuration,
with evidence explaining the changes.**

The core is just reflection followed by curation:

```python
reflection = reflector.reflect_snapshot(evidence, agent)
epoch = run_curation(agent, [reflection], curator, output="artifacts/curation")
adapted = MetaAgent.model_validate(epoch["agent"])
print(agent.diff(adapted))
```

Here is the complete customer-facing example using the small
[adapt function](simple_ace.py):

```python
from cookbook.simple_ace import adapt
from thearc import MetaAgent
from thearc.learning import EvidenceSnapshot

agent = MetaAgent.from_workspace("my-agent")
evidence = EvidenceSnapshot.load("my-session-evidence")

adapted = adapt(
    agent, evidence,
    backend="YOUR_BACKEND",
    model="YOUR_MODEL_ID",
    output="artifacts/first-ace-run",
)

print(agent.diff(adapted))
```

Run from the repository root. Replace `YOUR_BACKEND` with `antigravity`, `claude`,
or `codex`, and choose a model supported by that runtime. Install/authenticate
that runtime first; see [setup](../docs/reference/runtimes.md#current-runtime-support).
There is no default provider. Source-session format need not match the backend.

`my-agent` is a canonical workspace saved with `MetaAgent.to_workspace()`.
`my-session-evidence` is a saved snapshot of one or a few explicitly selected
sessions; see [snapshot example](README.md#1-freeze-sessions-as-evidence).
Use the configuration associated with those sessions where possible; the library
cannot prove which instructions were actually loaded during a historical session.

The function uses ordinary Python calls. It adds only input checks and artifact
saving around the core shown above—no flow engine, tracker or custom orchestration.
Calling it invokes real agents and can consume quota. It neither executes past
session commands nor installs the returned configuration into a project.

The new output directory contains the original config, evidence, reflection and
committed curation history. Existing output directories are refused. Inspect it:

```sh
thearc reflection show artifacts/first-ace-run/reflections
thearc curation show artifacts/first-ace-run/curation
thearc curation diff artifacts/first-ace-run/curation
```

Reflections rate specific configuration parts with reasons and citations; curation
uses those findings to make validated edits. A justified no-change result is valid.
This is a small-snapshot adaptation example, not a scalability demonstration or
proof of improved performance. It does not run evaluation or bump the release
version automatically. Review artifacts for sensitive information before sharing.
