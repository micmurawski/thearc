# Agentic episodic adaptation framework plan

The goal is a provider-neutral framework that learns updates for agent skills,
context, and hooks from episodic execution data while preserving evidence,
reviewability, and rollback.

## Phase 1 — stable learning contracts and run accounting

Status: first slice complete.

- Define typed episode, context snapshot, change set, commit, and checkpoint
  contracts. Implemented in `thearc.learning.contracts`.
- Separate rank-free reflection input from rank-aware curation input.
- Add lifecycle journal records for adaptation runs and flow stages. The toy
  flow now writes `RunJournal` records.
- Add checkpoint state for the flow runner. The first slice persists atomic
  start/completed/failed checkpoints; node-level resume is still TODO.
- Make checkpoint writes atomic. Resource-file transactions remain Phase 4.
- Keep the toy implementation as a deterministic contract test.

## Phase 2 — canonical episode model

- Introduce `Episode` as the learning unit above provider sessions and runs.
- Add time-range, tag, workspace, outcome, and provider filters.
- Persist episode projections and immutable evidence references.
- Add provider capability registration instead of a fixed harness literal.

## Phase 3 — storage for large archives

- Remove duplicated raw payloads from canonical SQLite rows.
- Store compressed, content-addressed raw records separately.
- Keep SQLite for metadata and FTS5 for searchable text.
- Use Parquet/DuckDB for batch analytics and an optional vector index for
  semantic retrieval.
- Add import, query, and search benchmarks with explicit budgets.

## Phase 4 — safe context mutation

- Add typed change operations for skills, Markdown sections, hooks, and future
  provider-specific resources.
- Add optimistic revisions, file locks, atomic multi-file commits, rollback,
  dry-run, approval, and conflict handling.
- Make journal writes part of the commit protocol.

## Phase 5 — durable flow execution

- Replace shared untyped flow dictionaries with typed run state.
- Persist node inputs, outputs, attempts, errors, and checkpoints.
- Add idempotency keys, retry classes, bounded concurrency, cancellation, and
  resume after process failure.
- Make the Flow graph the single canonical ACE execution path.

## Phase 6 — evaluation and governance

- Replay held-out episodes against baseline and candidate contexts.
- Add acceptance policies, human review, confidence, provenance, and rollback.
- Track model, prompt, token, latency, and cost metadata for every stage.
- Add privacy controls, redaction, retention, and access boundaries.

## Phase 7 — public framework release

- Split installer concerns from history/adaptation concerns at the package API.
- Add provider and storage plugin documentation.
- Publish compatibility guarantees, migration tooling, and reproducible
  examples.
- Replace generated repository artifacts with CI-produced reports.
