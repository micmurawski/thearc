# Experiment collection cleanup

## Diagnosis

The ten experimental sessions skipped for absent assistant/tool activity failed
before inference with `usage_limit_exceeded`. This is present in their native
`event_msg/task_complete` records, not inferred from reflection output. For example,
`logs/ace-fastapi-sessions-20260926/rollout-2026-09-26T21-25-54-01a0df2e-653a-7062-80f4-1aae8ab30c9c.jsonl`
ends with that error and no assistant response. They need fresh runs after account
quota is available; changing reflection logic cannot recover nonexistent behavior.

The previous runner compounded this problem:

- Timestamp-based selection could collect another concurrent Codex session.
- A log's existence was reported as capture success without checking activity.
- The documented shell loop did not stop on a failed prompt.
- The script's Antigravity name/docs did not match its Codex implementation.
- Branch changes carried uncommitted edits between supposedly isolated tasks.
- The global session-root ingestion command could include unrelated conversations.
- An interactive branch-restoration question could block unattended execution.

## Implemented

`thearc.learning` replaces the Python package `thearc.history`, covering session
indexing, reflection, evaluation, and ACE without adding a compatibility package
or duplicate classes. Public class names and SQLite storage remain unchanged.
The `sessions` CLI command retains `history` as an alias; both now expose
Antigravity ingestion/search alongside the other supported adapters.

`scripts/run_codex_prompts.py` and its shell wrapper replace the old runner.
They use the [documented Codex JSON event protocol](https://learn.chatgpt.com/docs/non-interactive-mode),
match thread ID and working directory, copy native evidence, and check that the
library indexes activity. Tool activity is required by default for these coding
tasks. Every attempt retains diagnostics; failures stop the batch and leave
unstarted prompts explicitly listed. Timeouts terminate the launched process group.
Only copied sessions are indexed in the output's `index.sqlite`.

The reflection-only flow now returns `no_evidence` (and the CLI exits nonzero)
when there are no usable sessions, rather than reporting successful generation.

## Remaining limitations / next improvements

1. **Task isolation (implemented).** Every prompt now uses a retained detached
   worktree at the same pinned commit. The source checkout and other prompts'
   files are not edited. Ignored/uncommitted configuration and dependencies are
   not copied; reproducible environment provisioning remains separate work.
   Worktrees share Git metadata, so they are not a security boundary.
2. **Historical configuration attribution.** New runs save their pre-run imported
   `MetaAgent`, but old sessions cannot establish exactly which configuration was
   installed. The reflection CLI still evaluates the chosen current `--project`
   configuration; automatically associating per-run snapshots with reflection
   inputs remains future work. Imported project snapshots also do not capture
   every global instruction or runtime setting.
3. **Collection versus quality.** Tool activity and successful completion prove
   only that a usable trajectory exists. They do not prove tests passed, that
   Graphify was used, or that a reflection's causal claims are correct. These
   need separate evaluation and review.
4. **Corpus provenance.** The old corpus contains 21 sessions for ten prompts,
   including retries and longer sessions. It is not a one-prompt/one-result
   benchmark. New manifests give an explicit prompt-to-thread mapping; old
   artifacts have been preserved, not relabeled or removed.

Validation is offline: real indexing plus a fake CLI exercises success, quota
failure, missing/wrong-project transcripts, missing indexed activity, response-only
tasks, malformed/multi-thread streams, timeouts, and wrapper invocation from a
different directory. No new paid inference was run during this cleanup.
