# Contributing and limits

## Run checks

From an activated environment in the repository root:

```sh
python -m pip install -e '.[dev,docs]'
git submodule update --init --recursive
python -m pytest -q
python -m ruff check thearc tests
python -m mkdocs build --strict
```

The FastAPI fixture is a separate repository; preserve its files and submodule
pointer unless intentionally changing the fixture. Normal tests use synthetic
runtime responses. Environment-gated runtime probes and live experiments are
separate checks, not proof supplied by the ordinary test suite.

## Code map

| Package | Responsibility |
| --- | --- |
| `thearc.models` | MetaAgent, resources, ranks, identity, workspace I/O, diffs |
| `thearc.agents` | Harness-specific configuration installers |
| `thearc.learning.sessions` | Native adapters, SQLite indexing, sequence patterns, translation, optional Arrow exports |
| `thearc.learning.evidence` | Snapshots, exports, scoped retrieval |
| `thearc.learning.reflection` | Evidence-based and native handoff reflection, runtime adapters, validation, artifacts, batch flow |
| `thearc.learning.curation` | Assessments, editing tools, candidates, epoch history |
| `thearc.learning.runtime` | Run records and offline handoffs |
| `thearc.learning.ace` | Pipeline composition, evaluation contracts, synthetic examples |
| `thearc.flow` | Optional flow composition and visualization |

Use `thearc.learning` for common imports or the focused package for a subsystem.
Compatibility aliases for older history/learning paths are centralized in
`thearc.learning._compat`; they are not separate implementations.

## Test layout

Tests mirror the subsystems they exercise:

| Directory | Coverage |
| --- | --- |
| `tests/models` | Configuration models, identity, workspaces, ranks, Markdown, legacy session search |
| `tests/agents` | Installers and the Graphify fixture |
| `tests/cli` | Top-level commands and aliases |
| `tests/learning/sessions` | Indexing, ordering, filtering, patterns, translation, exports |
| `tests/learning/evidence` | Snapshots, retrieval, offline context plans |
| `tests/learning/reflection` | Evidence reflection, native handoff, runtime adapters, reflection CLI |
| `tests/learning/curation` | Assessments, edits, backends, epoch history |
| `tests/learning/ace` | Pipeline composition and evaluation |
| `tests/flow` | Flow visualization and progress |
| `tests/cookbook`, `tests/scripts` | Executable recipes and collection workflows |
| `tests/repository` | Packaging, docs, release workflows |

Import compatibility tests live in `tests/learning/test_learning_layout.py`.
Shared repository and fixture locations are defined in `tests/paths.py`.
Run a subsystem with `python -m pytest tests/learning/sessions -q`.
The `tests/fastapi` submodule is fixture data and is excluded from collection.

Runtime tests are opt-in with `THEARC_TEST_CODEX_RUNTIME=1`; inspect each test's
requirements before running them. The native handoff runtime test uses a local
mock inference endpoint. Ordinary tests do not invoke paid models.

## Current limits

- Snapshots and some pipeline results materialize substantial data. Complete
  session enumeration does not make the pipeline bounded-memory.
- Evidence-based reflection uses bounded inline evidence. Native handoff reflection
  continues a full forked session and currently supports Codex. Retrieval tools
  are not automatically connected to either reflector.
- Curation candidates and epoch output can repeat configuration content.
- Durable whole-pipeline scheduling, concurrency, and recovery are not implemented
  by the simple recipe.
- Offline handoffs do not execute or resume native sessions.
- Recorded live runs are experiments, not evidence of general performance gains.
- SQLite storage and source synchronization target local, single-user workflows.

The [documentation archive](https://github.com/micmurawski/thearc/tree/HEAD/archive/documentation)
preserves design proposals, migration notes, and experiment reports. They are
historical context, not current API documentation. Confirm implementation before
turning a proposed feature into a user-facing claim.
