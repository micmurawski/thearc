# FastAPI isolated-worktree session corpus

Collected on 2026-09-27 by `scripts/run_codex_prompts.sh`, using the existing
Codex CLI model configuration. All ten prompts ran once in distinct detached
worktrees at commit `7940a15fbc523c0d8f811455fe815bffb4214195`.
The source checkout `tests/fastapi` remained clean at that commit.

## Contents

- `summary.json`: prompt-to-thread/worktree mapping and collection results.
- `sessions/`: ten exact native Codex rollout logs.
- `index.sqlite`: normalized corpus; source ID `codex-prompts`.
- `runs/<id>/`: extracted prompt, pre-run MetaAgent snapshot, event stream,
  stderr, final response, manifest, and tracked-file `changes.patch`.
- `worktrees/<id>/`: retained working copies, including untracked test files.

Verified: ten indexed sessions, 1,103 normalized events, and 306 assistant/tool
activity events. Every run passed collection checks and has indexed tool activity.
Collection success does not establish implementation correctness.

## Agent-reported validation outcomes

| Prompt | Outcome reported in final response |
| --- | --- |
| 01 Request ID | 6 targeted tests and 24 related tests passed |
| 02 Deprecation metadata | 25 targeted tests and 83 related tests passed |
| 03 Route response headers | 22 targeted tests passed; related suite: 113 passed, 3 skipped |
| 04 Strict slashes | 9 targeted tests and 52 related tests passed |
| 05 Rate-limit metadata | 22 new tests and 42 related tests passed |
| 06 Validation error codes | Syntax checks passed; pytest blocked by missing `starlette`, installation unavailable |
| 07 Conditional schema inclusion | 26 new tests and 120 related tests passed |
| 08 Startup-error callback | 9 targeted tests and 10 existing tests passed |
| 09 Exclude-none defaults | 30 targeted tests and 46 existing tests passed |
| 10 Route-class mapping | Syntax/basic lint passed; pytest blocked by missing `starlette`, package index unreachable |

These are the agents' reports, not an independent re-execution of their test suites.
Prompt 7 paused for several minutes before completing; its trace is preserved.
The failed sandbox-only launch in sibling `fastapi-worktrees-20260927/` is not
part of this ten-session corpus.

## Reflection input preview

From thearc's repository root:

```sh
.venv/bin/python scripts/generate_reflections.py \
  --index logs/codex-prompts/fastapi-worktrees-20260927-live/index.sqlite \
  --source-id codex-prompts \
  --project tests/fastapi \
  --model YOUR_CODEX_MODEL \
  --output logs/session-reflections/fastapi-worktrees-preview
```

This is an offline preview. Add `--execute` with a new output directory to
generate reflections using model quota. No reflection inference was run as part
of collection. The preview uses the current project configuration; each run's
original imported configuration is separately preserved in `agent-before.json`.

Raw artifacts may contain private source/session content. Review before sharing.
Worktree metadata and manifest paths are local to this checkout. Native JSONL
logs can be copied and re-indexed with thearc when moving the corpus elsewhere.
