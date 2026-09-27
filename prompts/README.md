# ACE Pipeline Trajectory Prompts — FastAPI Modifications

This directory contains 10 agent tasks that **modify the FastAPI source code** (`tests/fastapi/` subdirectory). They request reads, patches, and tests, but usable transcripts depend on successful agent execution. The collector checks completion and tool activity; quota/authentication failures are not successful experiments.

---

## Scope

All prompts are scoped to the [`tests/fastapi/`](../tests/fastapi) directory which contains a full local clone of the FastAPI repository with a pre-built graphify knowledge graph in `tests/fastapi/graphify-out/`.

**Run agents from `tests/fastapi/` as the working directory:**
```bash
./scripts/run_codex_prompts.sh prompts/01_add_request_id_header.md
```
The script defaults to `tests/fastapi/`, runs `codex exec --json`, matches the emitted thread ID and project directory to its native rollout, and copies that exact transcript. It honors `CODEX_HOME` (or `--sessions-dir`). It never selects the newest global session. See [official non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode) for the event protocol.

Every prompt runs in its own **detached Git worktree**, created under the output's
`worktrees/` directory. All start from the same commit, resolved once from source
`HEAD` (or `--base`). The original checkout's files, index, and branch are not
changed, and edits do not accumulate between prompts. There is no stash or reset.

Only committed files are checked out, including the committed Graphify configuration.
Uncommitted configuration changes, ignored graphs, virtual environments, and other
local files are not copied or symlinked. Dependencies must already be available to
the subprocess environment; the agent may create outputs in its own worktree.
Git worktrees share repository metadata and are not a security boundary. The runner
creates Git worktree registrations and output artifacts; it does not promise zero
filesystem writes. Codex still runs with its workspace-write sandbox.

---

## Prompt Matrix

| # | File | Feature Area | Key Source Files | ACE Signal |
|---|------|-------------|-----------------|------------|
| 01 | [`01_add_request_id_header.md`](./01_add_request_id_header.md) | Core Middleware | `applications.py` | Feature addition + UUID validation |
| 02 | [`02_deprecated_at_route_field.md`](./02_deprecated_at_route_field.md) | Router & OpenAPI | `routing.py`, `openapi/utils.py` | Schema extension + propagation |
| 03 | [`03_per_route_response_headers.md`](./03_per_route_response_headers.md) | Router | `routing.py`, `applications.py` | Response pipeline manipulation |
| 04 | [`04_strict_slashes_router.md`](./04_strict_slashes_router.md) | Router | `routing.py`, `applications.py` | Routing config + redirect behaviour |
| 05 | [`05_rate_limit_openapi_metadata.md`](./05_rate_limit_openapi_metadata.md) | OpenAPI Extension | `routing.py`, `openapi/utils.py` | Metadata injection into OpenAPI spec |
| 06 | [`06_structured_validation_error_code.md`](./06_structured_validation_error_code.md) | Exception Handling | `exception_handlers.py` | Error recovery + backwards-compat |
| 07 | [`07_include_in_schema_if.md`](./07_include_in_schema_if.md) | OpenAPI Filtering | `routing.py`, `openapi/utils.py` | Callable-conditional schema generation |
| 08 | [`08_on_startup_error_callback.md`](./08_on_startup_error_callback.md) | App Lifecycle | `applications.py` | Error handler plumbing + lifecycle |
| 09 | [`09_global_exclude_none_default.md`](./09_global_exclude_none_default.md) | Response Config | `applications.py`, `routing.py` | Global default propagation |
| 10 | [`10_route_class_map.md`](./10_route_class_map.md) | Router Extensibility | `routing.py` | Per-method route class dispatch |

---

## Running All Prompts

```bash
# Preview the entire batch without writing artifacts or launching Codex
./scripts/run_codex_prompts.sh --dry-run prompts/0*.md prompts/1*.md

# Run sequentially in isolated worktrees of tests/fastapi; stop on the FIRST failure
./scripts/run_codex_prompts.sh --model YOUR_CODEX_MODEL \
    --output logs/codex-prompts/fastapi-experiment \
    prompts/0*.md prompts/1*.md
```

---

## Indexing Trajectories into ACE

Output directories must be new and outside the target checkout. Each run saves the extracted prompt, pre-run `MetaAgent` snapshot, Git revision/status, JSON event stream, stderr, final response, and a manifest. Batch `summary.json` records failures and prompts not started; `sessions/` contains only exact matched native rollouts (including failed ones for diagnosis). Raw artifacts can contain private source content; review before sharing.

Each manifest records the worktree path, source checkout, and pinned base commit.
`changes.patch` includes tracked changes relative to that baseline; untracked
files are retained in the worktree. Worktrees are preserved on success, failure,
and timeout. After reviewing/archiving results, remove a specific worktree with
`git -C tests/fastapi worktree remove <exact-worktree-path>`; Git will refuse dirty
worktrees unless you explicitly resolve or discard their changes. The runner
never force-removes experiment results.

Success requires a zero exit status, a completed turn without a failed turn, a matching native rollout, and tool activity in both the CLI stream and indexed native evidence. Use `--allow-response-only` only for intentionally conversational tasks. `--timeout` defaults to 1800 seconds per prompt and terminates the process group on timeout. Success is a collection check, **not proof that the task's tests passed or that Graphify was used**.

The old `run_agy_prompt.py/.sh` entrypoints and branch/TUI flags were removed. Use the new collector for automated experiments and invoke the Codex TUI separately for interactive work.

The collector automatically creates `index.sqlite` with source ID `codex-prompts`.
Only the copied corpus is indexed, not every session in your global Codex directory:

```bash
# Sync and search
.venv/bin/thearc sessions --index logs/codex-prompts/fastapi-experiment/index.sqlite sync
.venv/bin/thearc sessions --index logs/codex-prompts/fastapi-experiment/index.sqlite search "routing.py" --kind tool_call

# Preview reflection inputs; add --execute after reviewing them
.venv/bin/python scripts/generate_reflections.py \
    --index logs/codex-prompts/fastapi-experiment/index.sqlite \
    --source-id codex-prompts --model YOUR_CODEX_MODEL \
    --output logs/session-reflections/fastapi-experiment
```
