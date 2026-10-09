# Your first ACE run

## 1. Index sessions

After [installation](installation.md), index existing logs:

```sh
thearc sessions --index artifacts/sessions.sqlite index \
  --source codex --source-id experiment --root /absolute/path/to/logs
thearc sessions --index artifacts/sessions.sqlite list
```

Choose the source format matching your logs: `codex`, `claude`, `antigravity`, or
`pi`. Indexing writes SQLite and leaves source logs unchanged.

## 2. Reflect and curate

The recipe in `cookbook/resource_ace.py` formats selected history, requests resource
assessments, validates event citations, and curates a configuration.

```python
from cookbook.resource_ace import adapt
from thearc import MetaAgent
from thearc.resources import SessionStore

agent = MetaAgent.from_workspace("my-agent")
with SessionStore("artifacts/sessions.sqlite") as store:
    session = store.get("CANONICAL_SESSION_ID")
trajectory = session.split_into_trajectories()[0]
result = adapt(agent, trajectory, implementation="codex", model="YOUR_MODEL_ID", output="artifacts/first-ace-run")
print(agent.diff(result.agent))
```

Use a new output directory. Calling `adapt` invokes the chosen runtime and saves
baseline configuration, `evidence.jsonl`, reflections, and committed curation.
The recipe returns a `CurationResult`; `.agent` is the adapted configuration.
Keep the full result when [merging multiple adaptations](../guides/merging.md).
The source session and input MetaAgent stay unchanged. Select a relevant trajectory
that fits the reflector's input limit. See [runtime setup](../reference/runtimes.md).

## 3. Inspect the result

```sh
thearc reflection show artifacts/first-ace-run/reflections
thearc curation show artifacts/first-ace-run/curation
thearc curation diff artifacts/first-ace-run/curation
```

A no-change result is valid. Evaluate the candidate before deciding to
[install it](../guides/metaagent.md#install-configuration).
