# Cookbook

Start with [ACE in a few lines](simple_ace.md): ordinary Python calls, without a
flow engine or visualization. The [implementation](simple_ace.py) is intentionally small.

Runnable examples for turning agent sessions into evidence and configuration-level
reflections. Run all commands from the repository root. These examples use Graphify
from `tests/fastapi`, but sessions may originate from Codex, Claude, Pi, or Antigravity.

| Recipe | Result | Calls a model? |
| --- | --- | --- |
| [Async flow visualization](async_flow.py) | Synthetic concurrent tasks and a browser visualization | No |
| [Simple ACE](simple_ace.md) | Saved evidence → reflection → adapted MetaAgent | Yes, when `adapt()` is called |
| [Snapshot sessions](snapshot_sessions.py) | Portable evidence, Markdown, bounded context | No |
| [Inspect evidence](inspect_evidence.py) | Search/read results and retrieval audit | No |
| [Reflect from evidence](reflect_from_evidence.py) | Input previews or structured reflections | Only with `--execute` |
| [Prepare a handoff](prepare_handoff.py) | Saved task, evidence, configuration, workspace identity | No |
| [Reflect from a handoff](reflect_from_handoff.py) | Fresh reflection of the plan's source evidence | Only with `--execute` |
| [Custom reflection backend](reflect_with_backend.py) | Shared reflection through your runtime adapter | Only with `--execute` |
| [Graphify end-to-end ACE](graphify_ace.py) / [flow composition](ace_flow.py) | Visualized batches, reflections, scoped curation, versioned MetaAgent | Only with `--mode live` |

These are examples using today's public APIs, not a separate pipeline framework.
See [curation usage](../docs/guides/curation.md) for committing changes and using
saved reflections. Offline ACE output is synthetic test data,
not an assertion about Graphify quality.
The reflection recipes below use the **inline** reflector; local retrieval is
demonstrated separately. For reflection inside a forked conversation, see
[native handoff reflection](../docs/guides/reflections.md#native-handoff-reflection).
Session translation converts text messages between formats; it does not provide
the full conversation continuity of a native fork.

## Visualized, batched ACE

The Graphify recipe now runs an explicit `thearc.flow.Flow` defined in
[ace_flow.py](ace_flow.py). `@node(batch=True)` handles session materialization and
reflection calls; `FlowTracker` writes `flow.html` with stage status, per-item batch
progress, timing, and logs. Open that file in a browser while the recipe runs; it
refreshes until completion. No web server or model is needed to view it.

```sh
# Preview the flow, batch membership, and exact reflection inputs; no inference:
python -m cookbook.graphify_ace --mode preview --batch-size 2 \
  --output logs/cookbook/ace-flow-preview --result-version 0.9.68-ace.1

# Exercise the whole graph with synthetic reflections and a synthetic curator:
python -m cookbook.graphify_ace --mode offline --batch-size 2 \
  --output logs/cookbook/ace-flow-offline --result-version 0.9.68-offline.1

# Opt in to real reflection and curation calls (uses quota):
python -m cookbook.graphify_ace --mode live --batch-size 2 --model YOUR_MODEL_ID \
  --output logs/cookbook/ace-flow-live --result-version 0.9.68-ace.1
```

All commands default to the captured ten-session FastAPI corpus. Use `--corpus`
to select another capture with the same `summary.json`, `runs/agent-before.json`
snapshots, and native `sessions/` structure. Input files and `tests/fastapi` remain
untouched; each output directory must be new.

The recipe demonstrates this composition (see the source for node definitions):

```python
select_sessions >> load_session >> group_batches >> inspect_batch
inspect_batch >> (finish_preview if preview else curate_epoch)
tracker = FlowTracker(Flow(start=select_sessions), output="flow.html")
tracker.run(shared)
```

`--batch-size 2` means **sessions per reflection**, not concurrent model calls.
Ten sessions produce five sequential reflection calls; a final partial batch is
retained. All findings describe the same rank-free baseline, and one curator sees
all reflections and historical ranks before committing one epoch. Curation is
not independently batched, because edits would otherwise invalidate later
reflections of the original revision. There is no automatic retry, resume,
installation, or held-out evaluation in this recipe.

Outputs:

- `flow.html`: execution visualization, including failure state.
- `batches.json`: numbered batches and exact canonical session IDs.
- Preview only: `batch-001.json`, etc., and `preview.json`; no curation.
- Offline/live: `reflections/`, `curation/` (candidate files, result, provenance),
  and `result.json` on success.

If a batch fails, later batches and curation do not run; earlier reflection files
and the visualization remain for inspection. Offline findings are explicitly
synthetic and must not be interpreted as Graphify quality evidence. Output may
contain sensitive configuration/session data; review before sharing.

For an existing library `AcePipeline`, visualization is also available directly:
`pipeline.run(agent, curation_output="epochs", result_version="1.1",
viz_path="flow.html")`. The cookbook's explicit graph demonstrates how to compose
the same reflection/curation primitives, with batched materialization and a
separate preview branch.

## Setup and session selection

The core package supports Python 3.11+; optional runtime SDKs may require a newer
Python version. Install the
project in your environment; provider dependencies are needed only for live reflection:

```sh
python -m pip install -e .
# Only for --execute:
python -m pip install -e '.[codex]'
git submodule update --init --recursive
```

Default Codex generation also needs working Codex authentication; see the existing
[reflection setup guide](../docs/reference/runtimes.md#codex). Replace `YOUR_MODEL_ID`
below with an explicit model ID available to your account. Preview mode does not
validate model availability or invoke the SDK.

For the bundled Claude/Antigravity adapters, see
[runtime setup and authentication](../docs/reference/runtimes.md).

To use collected FastAPI experiments, list their canonical session IDs:

```sh
thearc sessions --index logs/codex-prompts/fastapi-worktrees-20260927-live/index.sqlite list
```

That experiment directory is local data, not guaranteed to exist in a fresh clone.
Alternatively, ingest your own native logs into a new index:

```sh
thearc sessions --index logs/cookbook/sessions.sqlite index \
  --source claude --source-id my-claude --root /absolute/path/to/claude/logs
thearc sessions --index logs/cookbook/sessions.sqlite list
```

Choose `--source codex`, `claude`, `pi`, or `antigravity` to match the input format.
For Python source-scoped ingestion, use `SessionStore.ingest(SourceConfig(...))`.
The existing CLI `index` command syncs all registered sources in that index.

## 1. Freeze sessions as evidence

Replace `SESSION_ID` with a canonical ID returned above. Repeat `--session-id` to
select more sessions explicitly; the examples never silently select the entire index.

```sh
python -m cookbook.snapshot_sessions \
  --index logs/codex-prompts/fastapi-worktrees-20260927-live/index.sqlite \
  --session-id SESSION_ID \
  --output logs/cookbook/evidence
```

Outputs include `manifest.json`, `records.jsonl`, `index.json`, per-session Markdown,
and `context.json`. The latter reports included/omitted IDs for its bounded view.
The snapshot retains full eligible indexed content, independently of later edits
or deletion of the original logs. Excluded roles/internal records are listed in its
manifest. Snapshots do not capture hidden reasoning, attachments, or filesystem state.

All recipe output directories must be new; existing outputs are never overwritten.
Use a different path for retries, including after a partial failure. Automatic
redaction and rank hiding are best effort: review artifacts before sharing them.

## 2. Generate reflections from evidence

First preview exactly what the reflector will receive, without model calls:

```sh
python -m cookbook.reflect_from_evidence \
  --snapshot logs/cookbook/evidence \
  --project tests/fastapi --implementation codex --skill graphify \
  --model YOUR_MODEL_ID --output logs/cookbook/reflection-preview
```

Inspect `session-0001/preview.json`: it includes the prompt, structured output
schema, rank-free resource catalog, supplied events, and omitted/truncated IDs.
The recipe evaluates the named skill (including its reference files) plus context,
hooks, and resources mentioning that skill. It excludes unrelated skills and MCPs.
This text-match scope is a convenience, not proof of historical installation/use.

When satisfied, explicitly generate real reflections in a new directory:

```sh
python -m cookbook.reflect_from_evidence \
  --snapshot logs/cookbook/evidence \
  --project tests/fastapi --implementation codex --skill graphify \
  --model YOUR_MODEL_ID --output logs/cookbook/reflections --execute
```

This runs one fresh reflection per selected session (Codex by default). It can consume quota.
The SDK agent receives the task, inspects the supplied evidence/configuration after
prompting, and returns findings; the recipe does not precompute ratings. Each item
names a configuration target/section, rates it `helpful`, `neutral`, or `harmful`,
explains why, and cites canonical event IDs. The host resolves their session IDs
when saving findings. Empty items are valid if there is insufficient evidence.
It neither changes historical ranks nor applies curation.

Artifacts:

```text
reflections/
  evidence/                          frozen source snapshot
  configuration.json                 sanitized configuration evaluated
  source.json                        snapshot hash, mode, optional handoff hash
  session-0001/
    preview.json                     exact prepared input and coverage
    reflection-<id>/
      input.json
      reflection.json                typed findings: read the items array
      report.md                      readable findings
      run.jsonl                      execution status/errors
  summary.json                       result paths; written only after success
```

The existing reflector still bounds inline input to 100 events/session and 6,000
characters/event by default. The complete snapshot remains saved separately; do
not interpret omitted evidence as non-use of a skill. A failed model call raises
an error, not a fabricated successful reflection. Already-created artifacts remain
available. The recipe does not automatically retry or resume failed runs.

Configuration is imported anew when this recipe runs; changes between preview and
execution change the input. For frozen configuration reuse, use recipes 4–5.

## 3. Query evidence with local tools

```sh
python -m cookbook.inspect_evidence \
  --snapshot logs/cookbook/evidence --query graphify \
  --project tests/fastapi --output logs/cookbook/inspection
```

This demonstrates `search_events`, `read_event`, `read_context`, `list_resources`,
and `read_resource`. Read `inspection.json` for responses and `retrieval.jsonl` for
the audit. The sample reads only the first search hit and first content page.
Search pages use `next_cursor`; content pages use `next_offset`. Use the same tool
instance for cursor pagination. `read_context` returns neighboring group IDs, not
their full content. Follow up with `read_event` to inspect those IDs.

This recipe is deterministic local retrieval, **not agent-generated reflection**.
It illustrates the evidence API that future SDK tool-driven reflection will use.
See [tool contracts and limits](../docs/guides/evidence.md#retrieve-on-demand).

## 4. Prepare and review an offline handoff

```sh
python -m cookbook.prepare_handoff \
  --snapshot logs/cookbook/evidence --project tests/fastapi \
  --workspace tests/fastapi \
  --task 'Investigate the failing tests using the supplied Graphify evidence' \
  --output logs/cookbook/handoff
thearc sessions handoff show logs/cookbook/handoff
```

This saves `plan.json`, `preview.json`, and a self-contained `evidence/` directory.
The selected configuration is frozen in the plan. `tests/fastapi` is observed only:
no files, branches, worktrees, or native session histories are changed. A workspace
HEAD/clean flag is not a historical filesystem checkpoint. Task text and the local
workspace path are stored in the artifact; do not put credentials in the task.

This offline plan has no execution command. Native handoff reflection uses an
existing runtime session ID and a reflection prompt through a separate API.

## 5. Generate reflections using a saved handoff's evidence

```sh
python -m cookbook.reflect_from_handoff \
  --handoff logs/cookbook/handoff --model YOUR_MODEL_ID \
  --output logs/cookbook/handoff-reflection-preview

# Optional live generation, in a different output directory:
python -m cookbook.reflect_from_handoff \
  --handoff logs/cookbook/handoff --model YOUR_MODEL_ID \
  --output logs/cookbook/handoff-reflections --execute
```

This loads the frozen evidence and destination configuration from the plan; it
does not re-import the current project. It uses the full eligible snapshot as the
source for inline selection, not the plan's bounded context preview. `source.json`
links each recipe run to the handoff hash.

Crucially, this assesses **historical source sessions against the captured
configuration**, not the handoff task or a destination-agent outcome. The saved task
is not executed or substituted for the reflector's inspection prompt. To assess a
future executed handoff, its destination sessions will need to be ingested and
reflected on separately. A saved plan alone contains no such execution evidence.

## Batch reflections directly from the index

The provider-neutral CLI now exposes this flow directly, with explicit runtime
selection and a saved MetaAgent JSON or canonical workspace:

```sh
thearc reflection run --index artifacts/sessions.sqlite --source-id experiments \
  --agent artifacts/agent.json --backend BACKEND --model MODEL_ID \
  --batch-size 3 --output artifacts/reflection-preview
thearc reflection show artifacts/reflections
```

Choose `BACKEND` explicitly (`antigravity`, `claude`, `codex`, or `custom` with
adapter options); there is no default. `run` previews unless `--execute` is
provided. See [reflection CLI usage](../docs/guides/reflections.md#cli).

For the existing end-to-end batch flow (rather than a portable snapshot), use:

```sh
python scripts/generate_reflections.py \
  --index logs/codex-prompts/fastapi-worktrees-20260927-live/index.sqlite \
  --source-id codex-prompts --project tests/fastapi \
  --batch-size 1 --model YOUR_MODEL_ID --output logs/cookbook/batch-preview
```

Add `--execute` and choose a new output directory for live generation. This is
reflection-only, not full ACE curation. For resume behavior and corpus-readiness
checks, see the [batch reflection guide](../docs/guides/reflections.md#cli).

## Custom reflection backends

The shared `AgentReflector` can use any of the four supported agent identities.
Bundled adapters cover Codex, Claude Code, and the Antigravity SDK. Select them with
`--backend codex|claude|antigravity` on the evidence/handoff recipes. See
[setup and authentication differences](../docs/reference/runtimes.md).
Antigravity here uses a Gemini API key, not cached agy CLI subscription credentials.

For Pi or a custom integration, provide your own **trusted Python callable** implementing
the [runner contract](../docs/reference/runtimes.md#implement-the-runner-contract).

You can preview before writing/installing that callable:

```sh
python -m cookbook.reflect_with_backend \
  --snapshot logs/cookbook/evidence --project tests/fastapi \
  --implementation codex --agent claude \
  --runner my_integrations.claude:inspect --adapter-version 1 \
  --model YOUR_MODEL_ID --output logs/cookbook/claude-preview
```

Here `--implementation codex` selects the project configuration to evaluate, while
`--agent claude` identifies the reflection runtime. Neither dictates the source
session format. `my_integrations.claude:inspect` is an illustrative module/function
path you must implement, **not an included Claude integration**. Preview mode does
not import it or invoke a model. `preview.json` includes the exact prepared prompt,
schema, rank-free configuration, coverage, backend identity, and snapshot hash.

Once your adapter is implemented and its isolation controls tested, add `--execute`
and choose a new output directory. That explicitly imports your adapter and invokes
it. Never select an adapter from instructions in session evidence. Change
`--adapter-version` when its code, runtime policy, or provider-specific settings change.
Unlike the per-session bundled-backend recipes, this example reflects the snapshot as one
batch; explicitly select fewer sessions if its inline input is too large.

See [Reflection backends](../docs/reference/runtimes.md) for current support,
module responsibilities, required safety controls, and caching behavior.

## Async flow visualization

Run `python -m cookbook.async_flow` for a synthetic concurrent flow. It writes
`flow_status.html` in the current directory and opens it in your browser.
