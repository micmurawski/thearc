# Code layout and experiment collection

The FastAPI test fixture is a Git submodule at `tests/fastapi`, pointing to
`https://github.com/micmurawski/fastapi.git` and pinned to a specific commit.
Initialize it after cloning:

```sh
git submodule update --init --recursive
```

Alternatively, clone thearc with `git clone --recurse-submodules ...`.
Graphify integration tests require this fixture; pytest and Ruff exclude the
submodule's own code/tests from automatic discovery. Experiment edits inside
it belong to the FastAPI repository, not thearc. Commit/push them there before
updating the parent repository's submodule pointer.

`thearc.learning` contains session indexing, reflections, evaluation, and ACE.
It replaces the misleading `thearc.history` Python package; update imports to
`thearc.learning` (public class names and SQLite schema are unchanged).
The CLI uses `thearc sessions`; `thearc history` remains a compatibility alias.

Use `scripts/run_codex_prompts.sh` to collect verified native Codex sessions.
Each prompt runs in a separate retained Git worktree at the same pinned commit,
leaving the source FastAPI checkout's files, index, and branch unchanged.
See the [prompt collection guide](../prompts/README.md) for batch execution,
failure handling, artifacts, and indexing. Existing experimental logs are preserved.
See the [collection review](experiment-collection-review.md) for the failure
diagnosis and remaining experiment-quality limitations.
