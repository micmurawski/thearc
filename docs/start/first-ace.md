# Your first ACE run

This example indexes existing logs, selects evidence, then reflects and curates.
It does not replay session commands or install changes into your project.

## 1. Index sessions

From the repository root, after [installation](installation.md):

```sh
thearc sessions --index artifacts/sessions.sqlite index \
  --source YOUR_SOURCE --source-id experiment \
  --root /absolute/path/to/session/logs
thearc sessions --index artifacts/sessions.sqlite list
```

Replace `YOUR_SOURCE` with `antigravity`, `claude`, `codex`, or `pi`, matching the
log format. Choose a directory containing the sessions you intend to analyze.
Indexing reads logs and writes SQLite; no inference occurs.

## 2. Adapt configuration

Replace `YOUR_IMPLEMENTATION` with the harness whose configuration is installed
in your project, and set the project path. `YOUR_BACKEND` independently chooses
the runtime that performs reflection and curation; see [runtime support](../reference/runtimes.md).
Choose a model available to that runtime and adjust the cutoff to your experiment.

```python
from datetime import datetime, timezone

from cookbook.simple_ace import adapt
from thearc import MetaAgent
from thearc.learning import SearchFilters, SessionStore

agent = MetaAgent.from_project(
    "YOUR_IMPLEMENTATION", "/absolute/path/to/project", name="my-agent",
)

with SessionStore("artifacts/sessions.sqlite") as store:
    evidence = store.snapshot(filters=SearchFilters(
        source_ids=["experiment"],
        started_before=datetime(2026, 9, 1, tzinfo=timezone.utc),
    ))

adapted = adapt(
    agent, evidence,
    backend="YOUR_BACKEND",
    model="YOUR_MODEL_ID",
    output="artifacts/first-ace-run",
)
print(agent.diff(adapted))
```

`adapt()` is a small [cookbook helper](https://github.com/micmurawski/thearc/blob/HEAD/cookbook/simple_ace.py),
not a new framework abstraction. It prepares the evidence, saves inputs, calls
the reflector, and passes its findings to `run_curation()`. The
[reflection](../guides/reflections.md) and [curation](../guides/curation.md) guides
show those library calls separately.

Calling `adapt()` invokes real agents and can consume quota. Use a **new output
directory**, including after failed attempts. The original MetaAgent is unchanged.

The cutoff selects sessions that started strictly before that time; it does not
remove later events within them. `SearchFilters()` selects all indexed sessions.
No matching sessions raises an error. Keep this first snapshot small: reflector
input limits still apply, and the helper does not batch large collections.

## 3. Inspect the result

The output contains `baseline/`, `evidence/`, `reflections/`, and `curation/`.

```sh
thearc reflection show artifacts/first-ace-run/reflections
thearc curation show artifacts/first-ace-run/curation
thearc curation diff artifacts/first-ace-run/curation
```

Findings name configuration targets and cite session evidence. Curation records
edits and their reasons; a no-change result is valid. This example does not
evaluate performance or bump the version automatically.

When you are ready to deploy configuration, follow
[installation into agent harnesses](../guides/metaagent.md#install-configuration).
Review artifacts for sensitive information before sharing them.
