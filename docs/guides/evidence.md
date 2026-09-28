# Freeze and inspect evidence

An `EvidenceSnapshot` freezes selected indexed sessions into a portable input.
It is not a native agent session, a filesystem checkpoint, or permission to execute
recorded commands.

## Create and save

```python
from thearc.learning import EvidenceSnapshot, SearchFilters, SessionStore

with SessionStore("artifacts/sessions.sqlite") as store:
    evidence = store.snapshot(filters=SearchFilters(source_ids=["experiment"]))

evidence.save("artifacts/evidence")
loaded = EvidenceSnapshot.load("artifacts/evidence")
assert loaded.sha256 == evidence.sha256
```

Ingest sessions first; snapshot and load do not ingest or synchronize anything.
Supply exactly one of `filters` or `session_ids=["CANONICAL_ID"]` to `snapshot()`.
`SearchFilters()` explicitly selects all sessions. No matches raises `ValueError`.
Filtering and capture share one read transaction.

Snapshot filters support source IDs, harnesses, session IDs, and start-date bounds.
Event-only filters are rejected; use the separate `kinds` argument to restrict
eligible evidence kinds. Snapshot storage limits fail explicitly rather than
silently truncating content.

The saved directory contains `manifest.json` and `records.jsonl`. Later changes to
source logs do not alter it. Use a new destination directory; retry failed writes
at a new path. Content hashes detect changes, not authenticity of an untrusted file.

## Retrieve on demand

```python
from thearc.learning import render_context, session_tools, write_files

context = render_context(evidence, max_chars=20_000)
print(context.text)
print(context.omitted_event_ids)
write_files(evidence, "artifacts/readable-evidence", format="markdown")

tools = session_tools(evidence)
hits = tools.call("search_events", {"query": "graphify", "limit": 5})
print(hits)
```

Context rendering is a bounded view; omissions do not modify the snapshot.
The local retrieval tools support listing sessions, searching events, reading
events/context, and optional configuration-resource reads. Budgets and an audit
record what was delivered. These tools are not automatically connected to a
reflector runtime: bundled reflection currently uses bounded inline evidence.

## Prepare an offline handoff

```python
from thearc.learning import prepare_handoff, save_handoff

plan = prepare_handoff(
    evidence,
    task="Investigate the failing test",
    workspace="/absolute/path/to/existing/git/worktree",
)
print(plan.preview())
save_handoff(plan, "artifacts/handoff")
```

Current handoff support is **offline Codex context-plan preparation only**.
It records the task, evidence, and observed workspace identity. It does not start
an agent, restore a worktree, resume/fork a conversation, or import native history
into another harness. There is no handoff execution entry point yet.

## Privacy and coverage

Snapshots exclude raw/internal records, system/developer messages, and hidden
reasoning. Attachments are not materialized. Secret redaction and historical-rank
removal are best effort; inspect artifacts before sharing them. A snapshot can
only preserve what was indexed, not prove which instructions an agent actually used.
