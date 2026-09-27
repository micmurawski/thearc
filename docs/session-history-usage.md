# Local history search

This is the first implementation milestone. It searches local Pi, Codex, and
Claude Code JSONL transcripts through one interface. Sources are read-only.
Compatibility with the older SessionSearchEngine is not required.

## Python

```python
from pathlib import Path
from thearc.learning import HistoryService, SearchFilters, SearchQuery, SourceConfig

with HistoryService.open("./history.sqlite") as history:
    history.register_source(SourceConfig(
        id="my-codex", harness="codex", root=Path("~/.codex/sessions"),
    ))
    history.register_source(SourceConfig(
        id="my-claude", harness="claude", root=Path("~/.claude/projects"),
    ))
    history.register_source(SourceConfig(
        id="my-pi", harness="pi", root=Path("~/.pi/agent/sessions"),
    ))
    print(history.sync())
    page = history.search(SearchQuery(
        text="permission denied",
        filters=SearchFilters(kinds=["tool_result"], action_kinds=["shell.exec"]),
    ))
    for hit in page.hits:
        print(hit.event.harness, hit.snippet, hit.event.reference)
        print(history.read_evidence(hit.event.id))
```

Source IDs are stable caller-selected namespaces. Different machines or imported
archives should have different IDs. Re-registering an ID with a different root or
harness is rejected; root relocation/reconciliation is not yet supported.

`literal` is substring matching, case-sensitive by default. Set `case_sensitive=False`
for Unicode case folding. Returned ranges are offsets in normalized event text,
not byte offsets in the JSON source. Literal search currently scans filtered text;
it has no trigram acceleration or scan budget yet.

`lexical` interprets the input as an FTS5 token phrase and returns BM25 ranking.
It is case-insensitive under the FTS tokenizer, regardless of `case_sensitive`.
It does not yet support a boolean query AST. Neither mode executes historical tools.
System/developer messages, metadata, thinking, and summaries are excluded unless
`include_internal=True`. Images and unknown blocks retain raw evidence but are
not searchable text. Tool-call text is the serialized arguments.

Filters support source, harness, session, run, kind, role, normalized action,
native tool name, and known result status. Result actions/tool names can be resolved
from matching calls in the same run even when calls arrive in a later sync. There
is no inference of success from output strings. Dates, workspace filters,
active-branch selection, and regex are future work.

## Sessions and runs

Sessions are provider-neutral projections over canonical events. A `Session`
contains the stable internal ID, the provider's native ID, source and harness,
workspace, timestamps, and event/run counts. A `Run` represents one execution
context within a session, including delegated child runs.

```python
sessions = history.list_sessions(SearchFilters(harnesses=["codex"]))
session = history.get_session(sessions[0].id)
for run in history.list_runs(session.id):
    print(run.id, run.parent_run_id, run.event_count)
```

Use `session_id` and `run_id` from these objects in `SearchFilters`. The
provider's original session identifier remains available as `Session.native_id`;
internal IDs are scoped by source so identical native IDs from different
archives cannot collide. Session/run projections are rebuilt from canonical
events during sync and are also backfilled when an older index is opened.

Pagination uses `offset` and reports `index_revision`; continue against an unchanged
revision to avoid shifting results. Snapshot-pinned cursors are not implemented.

## CLI

```sh
thearc sessions --index ./history.sqlite index --source codex --root ~/.codex/sessions
thearc sessions --index ./history.sqlite index --source claude --root ~/.claude/projects
thearc sessions --index ./history.sqlite index --source pi --root ~/.pi/agent/sessions
thearc sessions --index ./history.sqlite sync
thearc sessions --index ./history.sqlite search 'permission denied' --kind tool_result
thearc sessions --index ./history.sqlite search 'fix permissions' --mode lexical
thearc sessions --index ./history.sqlite show EVENT_ID --context 3
thearc sessions --index ./history.sqlite trace RUN_ID
```

CLI responses are JSON. `index` registers a source and syncs all registered sources.
`sync` refreshes them after restart without requiring source options again.

## Context and delegation

`get_context()` returns a bounded run neighborhood. Pi events with native tree IDs
use ancestry and content blocks from the selected record; sibling branches are
excluded. Descendant context is not guessed when a branch is ambiguous. Other
providers use source ordering within the run; this is not cross-run causal ordering.

`get_delegation_trace()` returns direct children, recursive descendants, and
recorded agent-tool calls. Claude child associations come from transcript directory
structure. Codex child lineage is recognized when `session_meta.source.subagent`
contains `thread_spawn.parent_thread_id`. This does not pair every spawn call with
a specific child or establish its final outcome. Pi extension delegation formats
are not recognized yet. A child-run association and an agent-spawn request are
different pieces of evidence; the API labels this distinction.

## Recovery and coverage

Only newline-terminated JSONL records advance the committed checkpoint. Malformed
complete records are skipped with persistent line diagnostics, and a partial final
record waits for a later sync. Canonical rows, FTS entries, and artifact checkpoints
commit together. Unexpected ingestion failures roll back the artifact transaction.

Committed-prefix hashing detects rewritten/truncated files, including replacements
of the same size. Changed artifacts are reindexed with a new generation; their old
events are replaced, not retained as an audit archive. Prefixes are reread on sync,
so this first implementation favors correctness over optimal large-file throughput.
Changed-during-read detection aborts ingestion for that artifact with a retry warning.

Missing sources retain indexed evidence with availability warnings. Sources that
change after the last sync can temporarily have stale availability; `read_evidence()`
verifies the referenced JSON record before returning it. Source purging and retention
controls are not implemented. This is a single-user local index with no access-control
service layer. Raw source objects are retained in index rows and JSON responses.

## Optional Arrow and Parquet

Install `pip install 'thearc[arrow]'` to enable:

```python
with HistoryService("./history.sqlite") as history:
    for batch in history.scan_events(
        filters=SearchFilters(kinds=["tool_result"]),
        columns=["id", "run_id", "action_kind", "status", "text"],
        batch_size=8192,
    ):
        print(batch)
    history.export_dataset("./snapshot-001")
```

Scans emit typed, bounded RecordBatches. SQL filters are pushed into the canonical
store; column selection currently happens after event deserialization. The scan
does not promise zero-copy conversion or columnar source projection. Timestamps
are currently nullable strings, preserving provider values rather than imposing
timezone assumptions. Raw objects and heterogeneous arguments are omitted from
this first export schema, while source locators and relationship IDs are retained.

Exports pin a SQLite read revision, write Parquet batches into a temporary directory,
then publish the new directory. Existing destinations are rejected. The manifest
lists the files, revision, schema version, filters, and coverage warnings. Use its
file list to read the dataset; `manifest.json` is not a Parquet file. Empty exports
have an empty file list. Checksums, partitioning, snapshot deletion, and advanced
schema evolution remain future work.

No semantic embeddings, nearest-neighbor similarity, workflow pattern matching,
large-output sidecar expansion, provider-version certification, or million-event
performance guarantees are included in this milestone.
