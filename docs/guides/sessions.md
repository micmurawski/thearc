# Index and select sessions

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
from thearc.learning import SessionStore, SourceConfig

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
from thearc.learning import SearchFilters, SessionStore

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
