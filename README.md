# thearc ⚡

`thearc` is an extensible meta-framework for collecting and exploring episodic sessions from AI agents. Rather than prescribing one agent design, it provides the abstractions needed to turn agent traces into evidence for evaluating and improving an agentic system’s context files, skills, hooks, prompts, and MCP integrations.

Build agentic context-engineering pipelines on top of `thearc`: inspect sessions, extract reusable lessons and failures, curate versioned updates, evaluate their effect, and install the resulting capabilities into agent environments. The framework is designed to let these pipelines evolve as new artifacts and evaluation methods emerge, drawing direction from work on [agentic context engineering](https://arxiv.org/pdf/2510.04618), [meta-skill evolution](https://arxiv.org/pdf/2607.05297), [self-evolving agent skills](https://arxiv.org/pdf/2608.02636), [self-evolving agents](https://arxiv.org/html/2507.21046v4), and [meta context engineering](https://arxiv.org/pdf/2601.21557).

## A simple ACE example

Give it your configuration and a past session. Get back an adapted configuration,
with evidence explaining the changes. No flow engine or tracker—just reflection
followed by curation.

First, index your existing native session logs into a local SQLite database:

```sh
thearc sessions --index sessions.sqlite index \
  --source YOUR_SOURCE --source-id my-experiments \
  --root /absolute/path/to/session/logs
thearc sessions --index sessions.sqlite list
```

Replace `YOUR_SOURCE` with `antigravity`, `claude`, `codex`, or `pi` to match the
log format. Indexing reads existing logs; it does not run an agent or modify those
logs. To refresh registered sources later, run
`thearc sessions --index sessions.sqlite sync`.

Then select evidence and adapt the configuration:

```python
from datetime import datetime, timezone
from pathlib import Path

from thearc import MetaAgent
from thearc.learning import EvidenceSnapshot, SearchFilters, SessionStore
from thearc.learning.curation import create_curator, run_curation
from thearc.learning.evidence.snapshot import check_destination
from thearc.learning.reflection import ReflectorConfig, create_reflector


def adapt(
    agent: MetaAgent, evidence: EvidenceSnapshot, *, backend: str, model: str,
    output: str | Path,
) -> MetaAgent:
    """Adapt one configuration from a small snapshot; preserve inputs and provenance."""
    config = ReflectorConfig(model=model)
    reflector = create_reflector(backend, config)
    curator = create_curator(backend, config)
    # Preflight before creating artifacts or invoking either runtime.
    reflector.prepare_snapshot(evidence, agent)
    output = check_destination(output, tuple(evidence.manifest["blocked_root_hashes"]))
    output.mkdir(parents=True, mode=0o700)
    agent.to_workspace(output / "baseline")
    evidence.save(output / "evidence")
    reflector.artifact_dir = output / "reflections"

    reflection = reflector.reflect_snapshot(evidence, agent)
    epoch = run_curation(agent, [reflection], curator, output=output / "curation")
    return MetaAgent.model_validate(epoch["agent"])


agent = MetaAgent.from_workspace("my-agent")

# Sessions must already be ingested into this index.
cutoff = datetime(2026, 9, 1, tzinfo=timezone.utc)
with SessionStore("sessions.sqlite") as store:
    evidence = store.snapshot(filters=SearchFilters(started_before=cutoff))

    adapted = adapt(
        agent, evidence,
        backend="YOUR_BACKEND",
        model="YOUR_MODEL_ID",
        output="artifacts/first-ace-run",
    )

    print(agent.diff(adapted))
```

Replace `YOUR_BACKEND` with `antigravity`, `claude`, or `codex`, and choose a model
supported by that runtime. Install/authenticate the runtime first; see
[runtime setup](docs/reference/runtimes.md#current-runtime-support). There is no
default provider, and the source-session format need not match the backend.

`my-agent` is a canonical workspace saved with `MetaAgent.to_workspace()`.
`sessions.sqlite` must already contain ingested sessions; see
[session ingestion](docs/guides/sessions.md). The example selects sessions
whose indexed `started_at` is **strictly before September 1, 2026, at midnight UTC**.
Change `cutoff` to your desired timezone-aware datetime. Indexed start times use
the earliest valid, timezone-aware event timestamp; sessions without one do not
match date filters. Filtering happens in SQLite across registered sources, without
a pagination limit. No matches produces a clear error. Use `SearchFilters()` to
snapshot all indexed sessions, or add `source_ids=["my-experiments"]` to limit scope.

This selects whole sessions by start time, not individual events: a selected
session may contain activity after the cutoff. It does not imply the session has
finished. Snapshotting does not ingest or refresh logs.
Pass the snapshot directly to `adapt()`; it saves the evidence under
`artifacts/first-ace-run/evidence`, so no intermediate save/load is necessary.
Later, reload it with `EvidenceSnapshot.load("artifacts/first-ace-run/evidence")`.

Use a small index containing sessions relevant to this configuration. Snapshot
storage and reflector input limits still apply; this example does not batch large
collections or guarantee that every event reaches the reflector. Use the
[batched cookbook](cookbook/README.md#visualized-batched-ace) for larger collections.
Use the configuration associated with those sessions where possible; the library
cannot prove which instructions were loaded during a historical session.

Calling `adapt()` invokes real agents and can consume quota. It preserves the
original configuration and saves the baseline, evidence, reflections, and committed
curation history in a new output directory. Existing output directories are refused.
Inspect the findings and changes with:

```sh
thearc reflection show artifacts/first-ace-run/reflections
thearc curation show artifacts/first-ace-run/curation
thearc curation diff artifacts/first-ace-run/curation
```

Reflections rate specific configuration parts with reasons and citations; curation
uses those findings to make validated edits. A no-change result is valid. This
small-snapshot example does not replay session commands, install the result, run
evaluation, or bump the release version. An edit alone is not proof of improved
performance. Review artifacts for sensitive information before sharing.

The same function is available in [cookbook/simple_ace.py](cookbook/simple_ace.py).

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

Browse the [documentation](docs/index.md), or preview the MkDocs site locally:

```sh
python -m pip install -e '.[docs]'
python -m mkdocs serve
```

For runnable evidence, reflection, and handoff examples, see the [cookbook](cookbook/README.md).
