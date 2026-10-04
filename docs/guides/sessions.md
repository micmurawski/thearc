# Index and select sessions

For immutable `Session` objects, scoped search, Markdown rendering, and
`session.through(trajectory)`, see [Work with immutable sessions](resources.md).

Native transcripts are the source of evidence. `SessionStore` indexes them in
SQLite and exposes provider-neutral sessions, runs, messages, and tool activity.
Source files remain unchanged.

## Ingest logs

```sh
thearc sessions --index artifacts/sessions.sqlite index \
  --source YOUR_SOURCE --source-id experiment --root /path/to/logs
thearc sessions --index artifacts/sessions.sqlite list
thearc sessions --index artifacts/sessions.sqlite search "graphify"
```

`YOUR_SOURCE` is `antigravity`, `claude`, `codex`, or `pi`. Source IDs distinguish
collections; canonical session IDs are scoped to sources. The provider's native
session ID is not interchangeable with the canonical ID returned by `list`.
The CLI `index` command registers a source and synchronizes all registered sources.
Use `sync` to refresh them later.

Python ingestion can target just one source:

```python
from pathlib import Path
from thearc.learning.sessions.store import SessionStore
from thearc.learning.sessions.models import SourceConfig

with SessionStore("artifacts/sessions.sqlite") as store:
    report = store.ingest(SourceConfig(
        id="experiment", harness="claude", root=Path("/path/to/logs"),
    ))
    print(report.warnings)
```

Here `claude` is an input-format example, not a choice of reflection backend.
Indexing and queries never invoke a model. Inspect ingestion warnings: unsupported
or incomplete records can leave gaps in the available evidence.

## Select by date or source

```python
from datetime import datetime, timezone
from thearc.learning.sessions.models import SearchFilters
from thearc.learning.sessions.store import SessionStore

with SessionStore("artifacts/sessions.sqlite") as store:
    evidence = store.snapshot(filters=SearchFilters(
        source_ids=["experiment"],
        started_before=datetime(2026, 9, 1, tzinfo=timezone.utc),
    ))
```

`started_before` and `started_after` use exclusive bounds and require timezone-aware
datetimes. Start time is the earliest valid timezone-aware timestamp among indexed
events, not proof of a lifecycle boundary. Sessions without a known start do not
match date filters. Selected sessions may contain events after the cutoff.

Dates are normalized to UTC before comparison. Older indexes rebuild session/run
timestamp projections once from stored events when opened; raw event timestamps
and source logs are unchanged. This migration advances the index revision.

`iter_sessions(filters)` enumerates all matches without a page limit.
`list_sessions(filters, limit=100, offset=0)` is paginated, with a maximum page size
of 1,000. Consume iterators before closing or writing through their store.

## Search and inspect

`SearchQuery(text="graphify", filters=SearchFilters(...))` supports literal and
lexical search through `store.search()`. Filters include source, harness, session,
run, event kind, role, tool, action, and status. Date filters in event queries select
events belonging to matching sessions, not events occurring in a time range.

CLI inspection uses `show EVENT_ID` for neighboring events and `trace RUN_ID` for
recorded delegation relationships. These are observations, not replay commands.
System/developer messages and internal records are excluded from normal search.

Indexes retain previously read events when source logs disappear. Querying does
not rescan the source. If you need a portable, fixed input, create an
[evidence snapshot](evidence.md).

## Split a session into trajectories

A trajectory is the activity triggered by one user prompt:
`S = (p0, t0, p1, t1, ...)`. The prompt is separate from its response events.

```python
from thearc.resources import SessionStore

with SessionStore("artifacts/sessions.sqlite") as store:
    session = store.get("CANONICAL_SESSION_ID")
split = session.split_into_trajectories()
for trajectory in split:
    print(trajectory.prompt_text, trajectory.boundary)
print(split.unassigned_events)

# With a configured Reflector:
# result = reflector.handoff("Reflect on this task.", split[0])
# result = reflector.handoff("Reflect with earlier context.", session.through(split[0]))
```

`split_trajectories(ordered_events)` is also available for already-loaded events.
Splitting uses all indexed event kinds, including recorded reasoning and metadata.
Tool results never start trajectories. Recorded Codex user-message markers
distinguish native environment setup from actual prompts. Adjacent content blocks belonging to
one native record form one prompt. Different runs/artifacts are processed
independently; subagent trajectories cannot be mapped to the parent session's
native handoff reference.

`boundary` records what ended the extracted segment: `completed`, `interrupted`,
`next_prompt`, `stream_boundary`, or `end_of_stream`. The latter three do not prove
completion. Completion and turn-ID extraction currently recognize Codex lifecycle
records; other formats still split at normalized user messages. Events without a
preceding prompt, and metadata outside a completed response, remain unassigned.
Missing logs cannot be reconstructed by the splitter.

Trajectory handoff selects that prompt and response. Use `session.through(t)` to
include the conversation prefix. Handoff uses a native cutoff when possible, or
imports supported normalized events into the reflector runtime.

## Match behavioral sequences

`PatternScanner` uses SQLite to select candidate sessions, then loads one complete
session at a time and matches its ordered events in Python. It does not invoke a
model or modify transcripts.

```python
from thearc.learning.sessions.scanner import PatternScanner
from thearc.learning.sessions.models import SearchFilters
from thearc.learning.sessions.store import SessionStore

with SessionStore("artifacts/sessions.sqlite") as store:
    scanner = PatternScanner(store)
    matches = scanner.scan(filters=SearchFilters(statuses=["failure"]))
    # Equivalent: scanner.scan(statuses=["failure"])
    for match in matches:
        print(match.pattern_name, match.session_id, match.event_ids)
        print(match.start_index, match.end_index, match.context)
```

Event filters select **sessions containing a matching event**. Multiple conditions
must match the same event. They do not remove events from the sequence: selecting
failures still loads the preceding call and subsequent retry. Date bounds apply
to session start. Candidate selection uses `store.iter_candidate_sessions(filters)`
and has no page limit.

The default patterns are:

- `StubbornToolLoop()`: adjacent tool call → failed result → call to the same named
  tool. Known call IDs must pair correctly. Arguments may differ, and the retry
  may later succeed. `min_repeats=3` requires call → failure → call → failure → call.
- `FrustrationSpike(window=5, threshold=3)`: at least three failures in five
  consecutive **events**. This measures events, not conversation turns.

Both return overlapping matching windows. Indexed failures use `status="failure"`;
patterns also accept `"error"` in manually supplied events. Tool calls use
`kind="tool_call"`; `action_kind` describes the action category.

Matching preserves native record order, including intervening messages and
internal records. A run, source, session, or artifact boundary ends a sequence;
separate streams are not concatenated into artificial adjacency. A `PatternMatch`
contains the exact event tuple and zero-based start/end indices (end inclusive)
into the complete session event list. When using `scan_events(events)`, indices
refer to the supplied list, whose order is preserved.

These are behavioral signals for inspection, not proof that a retry was mistaken
or that an agent experienced frustration. Additional patterns (`SelfOverwrite`, `IgnoredUserInstruction`, `EmptyToolResult`,
`AbandonedFix`, `LongSilence`) are available in
`thearc.learning.sessions.patterns` for explicit selection. Import pattern classes directly from that module.

### Add a pattern

Implement `BehavioralPattern.find_matches(events)` with a loop or state machine.
The scanner supplies a contiguous stream from one run and adjusts returned
indices back to the original session positions.

```python
from thearc.learning.sessions.patterns import BehavioralPattern
from thearc.learning.sessions.patterns import PatternMatch
from thearc.learning.sessions.scanner import PatternScanner

class RepeatedMessage(BehavioralPattern):
    name = "repeated_message"
    description = "Two adjacent assistant messages have identical text."

    def find_matches(self, events):
        matches = []
        for i in range(len(events) - 1):
            first, second = events[i:i + 2]
            if (first.kind == second.kind == "message"
                    and first.role == second.role == "assistant"
                    and first.text.strip() and first.text == second.text):
                matches.append(PatternMatch(
                    pattern_name=self.name, description=self.description,
                    session_id=first.session_id, start_index=i, end_index=i + 1,
                    events=(first, second),
                ))
        return matches

scanner = PatternScanner(patterns=[RepeatedMessage()])
# matches = scanner.scan_events(ordered_events)
```

Pass `patterns=[...]` to choose the registry, or `pattern_names=[...]` to restrict
a scan to registered names. `scan_session(canonical_session_id)` scans one indexed
session. `scanner.summary(matches)` returns counts by pattern and severity.

### Use a match for reflection

Use immutable resource scopes for new integrations:

```python
from thearc.resources import SessionStore, evidence_to_markdown
from thearc.learning.sessions.patterns import StubbornToolLoop

with SessionStore("sessions.sqlite") as store:
    session = store.get(session_id)
for match in session.match(StubbornToolLoop()):
    reflection = reflector.reflect(PROMPT + "\n\n" + evidence_to_markdown(match.events), agent=agent)
```

The operator chooses the evidence. For handoff, select a trajectory or
`session.through(trajectory)` and give the reflector a prompt focusing on the
matched behavior. Pattern matching does not invoke reflection automatically.

## Translate message formats

`SessionTranslator` converts text messages between provider formats and can export
Markdown. Translation does not preserve the complete indexed event stream,
tool-call relationships, or native session continuity. Use native handoff when
the reflector should continue the original conversation.

```python
from pathlib import Path
from thearc.learning.sessions.translation import SessionTranslator

translator = SessionTranslator(markdown_export_dir=Path("artifacts/translations"))
plan = translator.plan_from_path(
    "codex", "markdown", path=Path("/path/to/rollout-session.jsonl"),
)
print(plan.target_provider, plan.destination)  # Preview without writing.
translator.write(plan)  # Refuses to overwrite an existing output by default.
```

Every `ConversionPlan` carries `target_provider`, so the writer uses the planned
format even under custom directories. Include this field when constructing plans
directly. Set `overwrite=True` on `write()` only when replacing existing output is
intended.
