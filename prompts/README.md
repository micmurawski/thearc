# ACE Pipeline Trajectory Prompts — FastAPI Modifications

This directory contains 10 agent tasks that **modify the FastAPI source code** (`tests/fastapi/` subdirectory). They request reads, patches, and tests, but usable transcripts depend on successful agent execution. The collector checks completion and tool activity; quota/authentication failures are not successful experiments.

---

## Scope

All prompts are scoped to the [`tests/fastapi/`](../tests/fastapi) directory which contains a full local clone of the FastAPI repository with a pre-built graphify knowledge graph in `tests/fastapi/graphify-out/`.

## Collect sessions with any prepared agent

Use `scripts/run_agent_prompts.py` with `--harness pi`, `claude`, `opencode`, or
`codex`. The target defaults to `tests/fastapi`; use `--cwd /path/to/project` for
another Git checkout. OpenCode requires an explicit `--model`: headless runs can
choose a different default from the model selected in the interactive UI. Other
harnesses use their configured model unless you pass `--model`.

```bash
# Preview first: no worktrees, inference, or output files are created.
.venv/bin/python scripts/run_agent_prompts.py --harness pi --dry-run prompts/01_add_request_id_header.md

# Pi: trust the prepared project configuration in each new worktree.
.venv/bin/python scripts/run_agent_prompts.py --harness pi \
    --agent-arg=--approve --output logs/pi-prompts/fastapi \
    prompts/0*.md prompts/1*.md

# Claude: use the permissions already configured for non-interactive work.
.venv/bin/python scripts/run_agent_prompts.py --harness claude \
    --output logs/claude-prompts/fastapi prompts/0*.md prompts/1*.md

# OpenCode 2: uses a private server and `session export`.
.venv/bin/python scripts/run_agent_prompts.py --harness opencode \
    --model 'minimax/MiniMax-M3#thinking' \
    --output logs/opencode-prompts/fastapi prompts/0*.md prompts/1*.md

# The existing Codex entry point still uses the same shared collector.
./scripts/run_codex_prompts.sh prompts/01_add_request_id_header.md
```

`--bin /path/to/executable` selects a particular installation. OpenCode also falls
back to `~/.opencode/bin/opencode` when it is absent from PATH. `--model` is passed
to the selected CLI; Pi accepts `provider/model`, and OpenCode accepts
`provider/model#variant`. Existing credentials, global configuration, skills,
and hooks remain available through the CLI's normal loading rules.
Choose a model available through your configured provider; the MiniMax command
above is an example. The collector passes the same explicit model to every run.

Use repeated `--agent-arg=VALUE` options for harness-specific settings. For example,
Claude's `--agent-arg=--permission-mode --agent-arg=acceptEdits` permits file edits;
shell commands still need the permissions configured in Claude. OpenCode's
`--agent-arg=--auto` approves requests that its policy does not explicitly deny.
The collector does not add either permission option by default. Keep session,
resume, and output-format flags under the collector's control.

The collector matches the emitted session ID **and worktree directory** before
copying native evidence. Codex honors `CODEX_HOME`; Claude honors
`CLAUDE_CONFIG_DIR`. `--sessions-dir` overrides their native lookup roots.
Pi saves native sessions directly beneath each run's `native/` directory.
OpenCode exports the exact session to `session.json` and creates a lossless,
single-record JSONL copy for indexing. This follows the native
[OpenCode export envelope](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/export.ts)
with `info` and `messages`; tool calls, results, and reasoning are preserved.
Both older `info`/`parts` messages and OpenCode 2's typed messages with `content`
are indexed. The subprocess's `PWD` is set to its worktree as well as its working
directory, so OpenCode resolves the correct project.

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
filesystem writes. Codex runs with its workspace-write sandbox; other harnesses use their own configured permissions.

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

## Resume after a failure or outer shell timeout

Keep the existing output and add `--resume`. You can provide all original prompt
files or just the unfinished ones:

```bash
.venv/bin/python scripts/run_agent_prompts.py \
    --harness opencode --model 'minimax/MiniMax-M3#thinking' \
    --output logs/opencode-traning --resume prompts/10_*.md
```

Add `--dry-run` to list pending prompts without modifying the batch. Completed
prompts are skipped. Each failed or interrupted prompt starts a **new session and
worktree** at the original batch's pinned commit. Earlier attempts, logs, and
worktrees are preserved. This resumes collection of the batch; it does not
continue an agent's unfinished conversation.

The collector reconciles per-run manifests with `summary.json`. An attempt left
`running` by a killed collector is marked `interrupted`; a completed manifest
missing from the summary is recovered without rerunning it. `runs` retains every
attempt, `pending_prompts` lists prompts without a completed attempt, and
`not_started` lists prompts that have never been attempted. Duplicate prompt labels
keep separate positions; selecting a repeated label resumes all its unfinished
occurrences.

The source checkout, harness, and previously attempted prompt text must match.
Resume keeps the original commit even if source HEAD has advanced. An active
collector or agent holding the batch lock prevents a second collector from
starting. For batches created before lock support, ensure the old processes have
stopped before resuming.

---

## Indexing Trajectories into ACE

Output directories must be new unless `--resume` is used, and must be outside the target checkout. Each run saves the extracted prompt, pre-run `MetaAgent` snapshot, Git revision/status, JSON event stream, stderr, response text, and a manifest with the harness name. Batch `summary.json` records failures and prompts not started; `sessions/` contains only exact matched native transcripts (including failed ones for diagnosis). Raw artifacts can contain private source content; review before sharing.

Each manifest records the worktree path, source checkout, and pinned base commit.
`changes.patch` includes tracked changes relative to that baseline; untracked
files are retained in the worktree. Worktrees are preserved on success, failure,
and timeout. After reviewing/archiving results, remove a specific worktree with
`git -C tests/fastapi worktree remove <exact-worktree-path>`; Git will refuse dirty
worktrees unless you explicitly resolve or discard their changes. The runner
never force-removes experiment results.

Success requires a zero exit status, a successful terminal response (checked in the native export for OpenCode), a matching native transcript, and tool activity in both the CLI stream and indexed native evidence. Use `--allow-response-only` only for intentionally conversational tasks. `--timeout` defaults to 1800 seconds per prompt and terminates the process group on timeout. Success is a collection check, **not proof that the task's tests passed or that Graphify was used**.

### Producer snapshots

`agent-before.json` is an import of the **project** configuration at the pinned
commit. It is not a complete snapshot of global configuration or dynamically
loaded plugins. OpenCode currently imports project skills from `.agents/skills`
and `.opencode/skills`, plus `AGENTS.md` and `CLAUDE.md`.

If you maintain a complete canonical producer definition, pass
`--agent-snapshot /path/to/agent.json`. It is recorded for later reflection and
curation; this option does not install it or change the runtime's configuration.

### Use the indexed evidence

The collector creates `index.sqlite` with source ID `<harness>-prompts`.
Only the copied corpus is indexed:

```bash
.venv/bin/thearc sessions --index logs/pi-prompts/fastapi/index.sqlite list
.venv/bin/thearc sessions --index logs/pi-prompts/fastapi/index.sqlite search "routing.py" --kind tool_call
```

Use the [Graphify evidence recipe](../docs/examples/graphify.md) to select sessions
with SQLite filters, match behavioral sequences in Python, and put the matched
events into a reflection prompt. The same recipe works for all four harnesses.
For a combined corpus, ingest each batch's `sessions/` directory into one store
with a distinct source ID and its corresponding harness. Select successful runs
from `summary.json` when building a success-only dataset; failed runs are retained
as useful failure evidence as well.
