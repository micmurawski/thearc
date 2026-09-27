# Session history library execution plan

The new API has no compatibility requirement with SessionSearchEngine.

## Milestone 1 — usable local search foundation (this implementation)

- [x] Typed sources, events, references, queries, and results.
- [x] Independent Pi, Codex, and Claude Code file adapters.
- [x] SQLite canonical store and FTS5 lexical index.
- [x] Incremental complete-line ingestion, atomic checkpoints, rewrite detection,
      missing-source diagnostics, and malformed-record recovery.
- [x] Literal/lexical search with provider/session/run/action filters and pagination.
- [x] Call/result joins, branch-aware context, and observed child-run associations.
- [x] CLI index/search/show/trace commands and usage documentation.
- [x] Provider-shaped fixtures and recovery/search tests; run repository checks.

## Milestone 2 — bulk analysis and hardening

- [x] Optional Arrow RecordBatch scans and Parquet snapshot export (implemented early).
- [ ] Bounded parser record sizes, payload sidecars, and indexing omission reporting.
- [ ] Broader versioned fixtures, native duplicate/fork reconciliation, relocatable
      source roots, Pi extension delegation recognizers, and Codex API importer.
- [ ] Profile million-event representative corpora; improve changed-file hashing
      and batch ingestion based on measured throughput.
- [ ] Source purge and snapshot retention policies with end-to-end tests.

## Milestone 3 — semantic retrieval

- [ ] Versioned episode/chunk construction and embedding backend protocol.
- [ ] Resumable embedding queue, budget/freshness reports, filtered vector retrieval.
- [ ] Hybrid rank fusion, similar-event/episode interface, labeled recall evaluation.

## Milestone 4 — patterns and shared deployment

- [ ] Bounded action-sequence queries, evidence-backed aggregates, duplicate-aware
      root-task counts, and approximate similarity candidate generation.
- [ ] Trusted owner scope and authorization across every retrieval path before
      exposing the index as a multi-user service.
- [ ] Server backends only after benchmarks establish a need.

Release gate for each milestone: meaningful fixture tests, recovery behavior,
source citation integrity, documented unsupported features, and measured limits.
The first release is local, read-only with respect to source histories, and has no
semantic search or multi-user authorization claims.
