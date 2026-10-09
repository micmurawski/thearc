# Work with immutable sessions

The `thearc.resources` interface provides historical sessions, trajectories,
reflection, and curation. Existing `thearc.learning` imports and CLI commands retain
their earlier behavior.

## Select history

```python
from thearc.resources import SessionStore

with SessionStore("artifacts/sessions.sqlite") as store:
    session = store.get(session_id)

trajectories = session.split_into_trajectories()
trajectory = trajectories[0]
prefix = session.through(trajectory)

print(trajectory.prompt_text)
print(trajectory.to_markdown())
```

A trajectory exposes `prompt_events` separately from response `events`. Search,
rendering, and handoff include both. `session.through(t)` includes everything from
the session start through `t`; passing `t` alone selects only that prompt and response.
Unassigned records remain available as `trajectories.unassigned_events`.

Events and nested arguments are immutable. `event.model_dump()` returns a detached
dictionary. Session events load on first access and are cached. The first store read
after index changes captures a SQLite snapshot in `<index-stem>.artifacts/` (or
`artifact_dir=`). This copies index bytes without decoding every event and costs
disk space proportional to the index. Retain snapshots while resources use them;
automatic snapshot cleanup is not implemented. Later synchronization and store
closure do not change a captured resource.

Use `Session.from_events(events, metadata=...)` for captured canonical records,
`session.save(path)` for a portable artifact, and `Session.open(path)` to reopen it.
Saving refuses overwrite. Empty histories require explicit session metadata.
Prefixes spanning multiple runs/artifacts fail until their causal order is known.

## Search and render

```python
hits = session.search("SyntaxError", tool_name="shell")
hit = hits[0]
print(hit.event.id)
print(hit.trajectory)

failures = trajectory.search(status="failure").collect()
lexical_hits = session.search("invalid argument", mode="lexical")
```

Store, session, trajectory, and prefix searches share filters and native event
ordering. Literal search is case-sensitive by default; use `case_sensitive=False`
for Unicode case folding. Lexical search uses SQLite FTS5 phrase matching and is
case-insensitive. Search includes internal records; filter `kind` or `role` when
you want a narrower scope. `count()` and `collect()` consume the result set.
Store query membership is pinned on first evaluation. Unassigned hits have
`trajectory=None`.

`session.match(pattern)` matches complete scoped event streams in Python. Store
`match(pattern, **filters)` first selects candidate sessions in SQL and then loads
their complete streams. Filters do not create artificial event adjacency.

`to_jsonl()` retains canonical records, including raw provenance. `to_codex()` and
`to_claude()` return exports with `text`, `event_ids`, `transformations`, and
`save(path)`. `to_jsonl(format="codex")` returns the same provider export as text.
Unknown records, unsupported multimodal conversions, and orphan tool results fail
explicitly. Codex lifecycle records can be retained in Codex exports; they cannot
be represented as Claude conversation messages.
When translated tool results have an explicit status, Codex exports encode their
status and original text together so failure information is retained.

The CLI uses the same resource renderer:

```sh
thearc sessions --index artifacts/sessions.sqlite render SESSION_ID --trajectory 0
thearc sessions --index artifacts/sessions.sqlite render SESSION_ID --trajectory 1 --through
```

## Compose an evidence prompt

```python
from thearc.resources import Reflector, evidence_to_markdown

reflector = Reflector(implementation="codex", model="YOUR_MODEL_ID", artifact_dir="artifacts/reflections")
evidence_prompt = evidence_to_markdown(selected_events)
# Also accepts canonical JSON records or their JSON array encoding.
reflection = reflector.reflect(PROMPT + "\n\n" + evidence_prompt, agent=agent)
print(reflection.text)
```

You own event selection and prompt composition. The formatter preserves order,
IDs, content, and call relationships, without selecting or truncating events. It
omits harness metadata and shares its renderer with `trajectory.to_markdown()`.
There is no evidence argument or `evidence_view` option on this reflector.
A composed string alone cannot establish verified source-event provenance.

## Define the response schema

```python
from pydantic import Field
from thearc import ResourceTarget
from thearc.resources import ReflectionBaseModel


class Finding(ReflectionBaseModel):
    resource: ResourceTarget
    observation: str = Field(description="Observed behavior and the resource's contribution.")


class Report(ReflectionBaseModel):
    summary: str
    findings: list[Finding]


print(Report.to_json_schema())
reflection = reflector.reflect(
    PROMPT + "\n\n" + trajectory.to_markdown(),
    agent=agent,
    output_schema=Report,
    validate_resources=True,
)
report = reflection.output  # Report instance
```

Field descriptions and nested definitions are included in the JSON Schema embedded
in the request. The host parses and validates the actual response. Raw JSON Schema
is also supported, returning decoded JSON instead of a model instance. Remote schema
references are rejected. `validate_resources=True` additionally checks recursively
annotated resource references against the supplied MetaAgent snapshot. Missing
agents fail before inference; failures include output locations and preserve the
invalid response. `report.validate_against(agent)` performs the semantic check
independently. Plain strings named `resource` do not receive guessed addressing.

Generated resource definitions carry `x-thearc-resource: true`; raw schemas use the
same annotation. `x-thearc-change-target` marks operation targets whose validation
depends on ADD/EDIT/REMOVE. Curation validates those operations on a candidate.

`ResourceAssessmentReport` supplies the standard resource-rating schema. Call
`reflection.assessments(history)` to validate its event IDs and adapt it to the
existing assessment ledger. This is separate from schema and resource existence
checks. Custom text does not automatically become a rating.

## Hand off history

```python
reflection = reflector.handoff(PROMPT, trajectory, agent=agent, output_schema=Report)
reflection = reflector.handoff(PROMPT, session.through(trajectory), agent=agent)
```

Handoff supplies selected events through native conversation history. It appends
your prompt, optional resource catalog, and output schema without a duplicate
transcript. The response is `reflection.text`; `reflection.execution` records the
reflector session, and `reflection.history` retains the selected immutable resource.

The Codex adapter uses an exact captured native turn for indexed Codex prefixes.
Representable isolated trajectories and other providers' messages/tool interactions
use a fresh persisted Codex conversation and `thread/inject_items`. It verifies the
persisted items before inference. Known Codex lifecycle records remain in captured
provenance and `history.json`, with their IDs explicitly reported as outside model
conversation items. Unsupported records fail before execution. Native source records
must still match the captured artifact; there is no fallback to a newer source turn.
Import requires complete tool call/result pairs. Incomplete histories can still be
rendered into an evidence prompt; the adapter does not invent missing tool results.

## Reference and active configuration

`agent` is the evaluated `MetaAgent`. Its resource catalog is reference material by
default. `agent_mode="active"` additionally applies supported resources to this run.
It does not install configuration into your project or change later calls.

| Capability | Current support |
| --- | --- |
| Prompt reflection | Codex; Claude/Antigravity with an object output schema; trusted custom runners |
| Captured native prefix fork | Indexed Codex main-stream histories with a recorded turn boundary |
| Translated history import | Codex destination; text, correlated tool calls/results, supported reasoning |
| Active configuration | Built-in Codex: context documents and rules as developer instructions |
| Skills, hooks, MCPs, commands, workflows in active mode | Rejected before execution; adapter activation is not implemented |
| Handoff to Claude/Antigravity | Not implemented; use caller-composed evidence prompts |

These are repository capabilities verified against pinned adapters. Local mock-model
tests check Codex request history, persistence, isolation, and activation boundaries;
they do not establish model quality. Evaluator runtime/model settings remain owned
by the reflector. Active subject instructions apply at developer priority; the
evaluator's base reflection instructions remain in place.

`reflector.prepare(...)` previews prompt composition and validates requested schema
and activation options without starting a runtime. Optional `artifact_dir` stores
requests, schemas, original responses, destination receipts, and failure records.
`Reflection.load(path)` reads saved results without the original Python schema
class; its `output` is decoded JSON. `thearc reflection show PATH` reads these versioned artifacts.

## Curate a revised MetaAgent

```python
from thearc.resources import Curator

curator = Curator(implementation="codex", model="YOUR_MODEL_ID")
result = curator.curate(
    CURATION_PROMPT,
    [reflection],
    agent=agent,
    output="artifacts/curation",  # Omit for a detached result without a saved epoch.
)
updated_agent = result.agent
print(agent.diff(updated_agent))
```

`CurationBaseModel` shares schema generation and reference validation.
`CurationReport` provides a standard summary and addressed changes. Custom schemas
require an explicit `adapter` returning a `CurationReport` before changes can apply.
`ADD` requires a new address and any required parent; `EDIT` and `REMOVE` require
existing targets. Validation follows operation order on a detached candidate.

The facade retains existing prose-edit restrictions, assessment deduplication,
revision checks, candidate replay, workspace validation, and epoch commits. Disabling
optional reference checks cannot bypass those rules. Text and custom reflections
can justify changes without creating rank assessments; supply explicitly validated
assessment artifacts to update ranks. Reflections must identify the configuration
revision being curated. The curator sees resource/rank/change history but does not
activate the evaluated configuration. A no-change result is valid.

For live Codex curation, reflections returned by `handoff()` retain their immutable
history and expose a bounded `arc_evidence` tool to inspect a cited event on demand.
The tool cannot read outside the selected scope. Plain prompt strings and reopened
reflection files do not automatically reconstruct those source handles.

## Merge parallel adaptations

Keep the `CurationResult` from each branch and pass the results, together with their
shared baseline, to `Merger`. See [merging adaptations](merging.md) for structural
merging, typed conflict resolution, consistency review, and assessment deduplication.
