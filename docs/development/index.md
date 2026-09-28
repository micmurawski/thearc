# Contributing and limits

## Run checks

From an activated environment in the repository root:

```sh
python -m pip install -e '.[dev,docs]'
python -m pip install pytest
git submodule update --init --recursive
python -m pytest -q
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
| `thearc.learning.sessions` | Native adapters, SQLite indexing, queries, optional Arrow exports |
| `thearc.learning.evidence` | Snapshots, exports, scoped retrieval |
| `thearc.learning.reflection` | Preparation, runtime adapters, validation, artifacts, batch flow |
| `thearc.learning.curation` | Assessments, editing tools, candidates, epoch history |
| `thearc.learning.runtime` | Run records and offline handoffs |
| `thearc.learning.ace` | Pipeline composition, evaluation contracts, synthetic examples |
| `thearc.flow` | Optional flow composition and visualization |

Use `thearc.learning` for common imports or the focused package for a subsystem.
Compatibility aliases for older history/learning paths are centralized in
`thearc.learning._compat`; they are not separate implementations.

## Current limits

- Snapshots and some pipeline results materialize substantial data. Complete
  session enumeration does not make the pipeline bounded-memory.
- Reflection uses bounded inline evidence, not automatic retrieval-driven input.
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
