# Graphify adaptation examples

These recipes keep the main steps explicit. Each defines its own `ReflectionSchema`.
The source session format and the runtime used for reflection are separate choices.

## 1. Indexed sessions → behavioral matches → evidence reflection

`cookbook/graphify_evidence.py` contains one `adapt()` function. The caller supplies
the index, filters, pattern, reflector, and curator:

```python
from pathlib import Path

from cookbook.graphify_evidence import adapt
from cookbook.graphify_patterns import ToolRetryAfterFailure
from thearc import MetaAgent
from thearc.learning.sessions.models import SearchFilters, SourceConfig
from thearc.resources import Curator, Reflector, SessionStore

batch = Path("logs/codex-prompts/fastapi-worktrees-20260927-live")
agent = MetaAgent.model_validate_json(
    (batch / "runs/06babf7d18ca4332bb93cc0028995502/agent-before.json").read_text()
)
reflector = Reflector(implementation="claude", model="YOUR_CLAUDE_MODEL")
curator = Curator(implementation="claude", model="YOUR_CLAUDE_MODEL")

with SessionStore("artifacts/graphify.sqlite") as store:
    store.ingest(SourceConfig(id="graphify", harness="codex", root=batch / "sessions"))
    result = adapt(
        agent, store,
        filters=SearchFilters(source_ids=["graphify"], statuses=["failure"]),
        pattern=ToolRetryAfterFailure(),
        reflector=reflector, curator=curator,
        output="artifacts/graphify-curation",
    )
if result is not None:
    print(agent.diff(result.agent))
```

The function does five things:

1. Calls `store.match(pattern, **filters.model_dump(exclude_none=True))`.
2. Formats the matched windows as Markdown evidence, with a heading for each span.
3. Calls `reflector.reflect(PROMPT + EVIDENCE, output_schema=ReflectionSchema, ...)` once.
4. Reads `response.output.reflections` and validates each item's resource references and event citations.
5. Passes the response containing all reflections to `curator.curate()`.

SQLite filters select candidate **sessions**. Python matching sees their complete
ordered event streams, so a failure filter keeps the preceding call and subsequent
retry available. Only matched windows go into the reflection prompt. No matches
means `None` and no model calls. Choose filters that keep the evidence within the
reflector's input limit; the example does not silently truncate it.

`ToolRetryAfterFailure` is a small example matcher in `cookbook/graphify_patterns.py`.
It finds a failed call followed by another call to the same tool within 20 events,
retaining intervening commentary. A new user prompt or another tool call ends the
pending sequence. It identifies retries for review, without assuming they were harmful.
You can instead supply `StubbornToolLoop`, `FrustrationSpike`, or your own
[behavioral pattern](../guides/sessions.md#match-behavioral-sequences).

To inspect matches without inference:

```python
with SessionStore("artifacts/graphify.sqlite") as store:
    for match in store.match(ToolRetryAfterFailure(), statuses=["failure"]):
        print(match.pattern_name, match.session_id, match.event_ids)
```

`ReflectionSchema` extends `ReflectionBaseModel` and contains a `reflections` list.
Each item has its own `summary`, `findings`, and `limitations`; each finding includes
a resource, reason, rating, and supporting event IDs. One model call can return several
reflections. An empty list skips curation. The execution response keeps their shared
schema and provenance. This recipe uses the findings as observations for edits.
It does not propagate historical ratings to a target that may differ from the producer.

To index sessions from other harnesses, ingest another `SourceConfig` with a distinct
source ID and `harness="claude"`, `"antigravity"`, `"pi"`, or `"opencode"`. Their records become
canonical events before filtering and matching. The reflector can independently use
`implementation="codex"`, `"claude"`, `"antigravity"`, or `"custom"` with a runner.
See [runtime setup](../reference/runtimes.md).

Codex outputs with explicit structured `exit_code` fields now receive success/failure
status, including outputs wrapped in text blocks. Sync an existing index to reparse
those records. Error words alone do not establish failure status. Captured immutable
sessions and source transcripts remain unchanged.

## 2. Reflection through handoff

Select an immutable session or trajectory and pass it to the handoff recipe:

```python
from cookbook.graphify_handoff import ReflectionSchema, reflect
from thearc.resources import SessionStore

with SessionStore("artifacts/graphify.sqlite") as store:
    session = store.get("CANONICAL_SESSION_ID")
trajectory = session.split_into_trajectories()[0]

reflection = reflect(
    agent, trajectory, model="YOUR_CODEX_MODEL",
    output="artifacts/graphify-handoff-reflections",
)
print(reflection.output.model_dump_json(indent=2))
```

Internally this calls `Reflector.handoff(PROMPT, trajectory, output_schema=ReflectionSchema, ...)`.
The conversation itself supplies the evidence. Its schema contains `summary`,
`findings`, and `limitations`; each finding identifies a resource, observation,
supporting excerpt, and recommendation. `cookbook.graphify_handoff.adapt()` additionally
curates that reflection.

The built-in handoff adapter currently uses Codex. It translates representable source
history into Codex conversation items, or forks an eligible native prefix. Claude
and Antigravity currently support prompt evidence, not historical handoff. Handoff
never silently falls back to appending evidence text to a prompt.

## 3. Parallel candidates → merge

The parallel recipe reflects on each supplied trajectory, curates independent candidates
from one target baseline, and then passes the full candidates to `Merger`:

```python
from cookbook.graphify_inputs import load_runs
from cookbook.graphify_parallel import ReflectionSchema, adapt

agent, trajectories = load_runs(
    "logs/codex-prompts/fastapi-worktrees-20260927-live",
    ["06babf7d18ca4332bb93cc0028995502", "073aff39688148cc8c3c528b2c3a9b39"],
)
merged = adapt(
    agent, trajectories, implementation="claude", model="YOUR_CLAUDE_MODEL",
    max_workers=2, output="artifacts/graphify-parallel",
)
if merged.unresolved:
    print([issue.model_dump() for issue in merged.unresolved])
else:
    print(agent.diff(merged.agent))
```

Its `ReflectionSchema` extends `ResourceAssessmentReport`, requesting supported
resource findings and limitations of each run. Each worker owns its runtime objects
and output directory. Merging resolves conflicting edits and reviews the combined
guidance using the built-in `MergeReport` and `MergeReview` schemas.

For sessions produced by different configurations, supply a shared target `agent`
and `source_agents=[producer_a, producer_b]`. Every candidate starts from that target;
observations can transfer between configurations, while historical ratings do not.
The optional `load_inputs()` helper supports a manifest of mixed native sources or
saved Session artifacts:

```json
{
  "sessions": [
    {
      "source": {"id": "claude-run", "harness": "claude", "root": "claude-sessions"},
      "native_session_id": "NATIVE_SESSION_ID",
      "agent": "producer-agent.json",
      "trajectory": 0
    },
    {"artifact": "saved-session.json", "agent": "other-producer.json", "trajectory": 0}
  ]
}
```

```python
from cookbook.graphify_inputs import load_inputs

inputs = load_inputs("inputs.json", target=agent)
merged = adapt(
    inputs.agent, inputs.histories, source_agents=inputs.source_agents,
    implementation="antigravity", model="YOUR_GEMINI_MODEL",
    output="artifacts/graphify-mixed",
)
```

The evidence recipe uses the SQLite index directly and needs no input manifest.
Imports, ingestion, filtering, and matching make no model calls. Reflection and
curation invoke the configured runtime; parallel merging may invoke conflict resolution
and consistency review. Model judgments do not replace evaluation of the adapted agent.
