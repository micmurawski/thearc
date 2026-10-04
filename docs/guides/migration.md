# Migrating to the resource interface

The parallel batch API and compatibility aliases have been removed. This is a
breaking change; imports and saved artifacts are not silently adapted.

| Removed interface | Replacement |
| --- | --- |
| `thearc.learning` flat exports, `HistoryService`, old module aliases | Public resources from `thearc.resources`; index records from `thearc.learning.sessions.models` |
| `AgentReflector`, provider reflector classes, factories | `Reflector(implementation=..., model=...)` |
| `reflect_snapshot`, `SessionBundle`, materializers, selection policies | Select a Session/Trajectory and compose `PROMPT + evidence_to_markdown(events)` |
| `HandoffReflector`, public `NativeSessionRef` | `reflector.handoff(PROMPT, session_or_trajectory)` |
| Provider curators and curator factories | `Curator(implementation=..., model=...).curate(...)` |
| `AcePipeline`, toy ACE, batch flow helpers | Explicit Python composition; see `cookbook/resource_ace.py` |
| `SessionLog`, `SessionSearchEngine` | Indexed `SessionStore.search()` or scoped `Session.search()`; old clustering helpers are retired |
| `reflection run --backend`, batch/resume/evidence-view flags | `--implementation`, required `--prompt-file`, optional `--schema`; call once per chosen scope |
| Hidden `history`, `sessions snapshot/export/inspect/handoff` commands | `sessions`, `evidence`, and `handoff` command groups |
| `handoff reflect` alias | `reflection handoff` with `--index` and canonical `--session-id` |

`Reflection.load()` and `reflection show` accept current versioned resource artifacts.
Old ACE and handoff reflection files need an explicit external migration or regeneration.
Existing SQLite indexes and committed curation epochs retain their formats. The
internal `AssessmentReport` is a ledger input produced by
`reflection.assessments(history)`; it is distinct from the reflector's response.
Configuration revision validation no longer accepts hashes that omit the version field.

Standalone portable evidence snapshots, offline context plans, ingestion adapters,
assessment ledgers, and epoch validation remain available through their canonical
modules. They serve separate storage and execution needs; the reflector does not
implicitly select or attach evidence from them.

## Unused scaffolding removed

The unimplemented episode/context contracts and adaptation checkpoint helpers were
removed: `Episode`, `EpisodeOutcome`, `EpisodeQuery`, `ContextSnapshot`, `RunCheckpoint`,
`CheckpointStore`, and `AdaptationRun`. `runtime/contracts.py` no longer exists.
Runtime journaling was also removed: `RunJournal`, `RunJournalRecord`, and
`runtime/journal.py`. Evidence retrieval no longer accepts `journal=` or retains
an `audit` call log. Retrieval limits and validation still apply.

The internal store's Arrow `scan_events()` and Parquet `export_dataset()` methods,
`sessions/columnar.py`, and the `arrow` installation extra were removed. Use
`iter_events()` for normalized events. `PatternScanner.scan_events()` continues
to match behavioral sequences and is unrelated to columnar export.

The duplicate decorator in `flow/utils.py`, unused `end`/`noop` nodes, and unused
`BatchFlow`, `AsyncBatchFlow`, and `AsyncParallelBatchFlow` classes were also removed.
Use `thearc.flow.decorators.node` for active synchronous, asynchronous, and batch
node composition with `Flow` or `AsyncFlow`.
