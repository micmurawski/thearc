# Codex-backed ACE reflections

The required sequence is **reflector prompt → Codex SDK agent inspects sessions against the configuration → structured reflection → host validation**. The SDK agent generates the findings; host code does not manufacture heuristic findings for the model to reformat.

The original sessions need not be Codex sessions. The existing history adapters normalize Codex, Claude, Antigravity, and Pi traces into `SessionBundle` inputs. No upload skill, foreign-session replay, or conversion into native Codex rollout files is required.

## Installation and authentication

```sh
python -m pip install -e '.[codex]'
```

The extra pins `openai-codex==0.157.1`. The Python SDK uses a local app-server and brings a matching CLI runtime; it is distinct from the OpenAI Agents SDK. The adapter uses the SDK's `Thread.run(..., output_schema=...)` binding. [Official Codex SDK documentation](https://learn.chatgpt.com/docs/codex-sdk)

Use an existing Codex login accessible to the runtime, or authenticate Codex separately before a live run. thearc does not read/copy credentials into artifacts, initiate login, or require credentials for dry runs and ordinary tests. The runtime must be able to access its own state directory; a surrounding sandbox may require permission for that, even though the analysis thread is read-only.

## Reflect on the entire experimental corpus

The standalone flow selects every session in the indexed `fastapi-prompt-batch` source,
checks for assessable activity, prepares bounded batches, generates reflections, and
saves a coverage report. It does **not** run curation, update ranks, or change the
`MetaAgent`. It evaluates Graphify instructions, nested reference files, and associated
project context/hooks from `tests/fastapi`.

Preview all inputs without invoking the SDK:

```sh
.venv/bin/python scripts/generate_reflections.py \
  --model YOUR_CODEX_MODEL \
  --output logs/session-reflections/experiment-preview
```

After reviewing the prepared inputs, generate real reflections (uses model quota):

```sh
.venv/bin/python scripts/generate_reflections.py \
  --model YOUR_CODEX_MODEL \
  --execute \
  --output logs/session-reflections/experiment-live
```

Repeat the live command with `--resume` to reuse matching completed reflections and
retry failed batches. Each invocation creates a separate `attempt-*` directory;
previous artifacts are preserved. Batches run sequentially, with at most three
sessions by default (`--batch-size`); the input-size budget can split them further.
A single session that cannot fit fails preparation before any model calls.
Independent batches continue after a reflection failure; the CLI exits nonzero if
any batch failed.
An empty or entirely skipped corpus returns `no_evidence` and a nonzero CLI exit
status instead of claiming successful reflection generation.

The current experimental index contains 21 sessions: the verified preview selects
11 sessions with assistant/tool activity, arranged in five batches, and explicitly
skips 10 sessions without that activity. Skipped sessions are not assigned neutral
ratings. Source failures after observable work remain eligible. The report includes
source error codes when available. Session evidence is bounded by the reflector's
limits; each batch artifact records omissions and truncations.

Outputs in the chosen directory:

- `attempt-*/selection.json`: every selected session, eligibility, and skip reasons.
- `attempt-*/batch-NNN.json`: exact prepared prompts, schema, and evidence manifests.
- `attempt-*/agent.json`, `manifest.json`, and `run.jsonl`: configuration snapshot,
  run settings, and progress journal.
- `attempt-*/summary.json` and `report.md`: coverage, batch failures, and result links.
- `reflection-*/reflection.json` and `report.md`: validated structured assessments
  and readable findings, created only by real generation (or reused on resume).

The CLI reads the existing index without syncing; use `--index` and repeat
`--source-id` to select other registered experimental sources. The library API
`run_reflections(history, meta_agent, reflector, output, source_ids=[...], execute=True)`
accepts any `MetaAgent` and normalized supported agent sessions. Omitting
`source_ids` at the library level selects all registered sources.

## Preview the collected Graphify example

Run from the repository root. List canonical session IDs from the existing index:

```sh
python scripts/ace_fastapi_sessions_demo.py --mode list
```

Choose the intended sessions explicitly; the copied corpus contains retries/additional rollouts, not a guaranteed one-to-one mapping between prompts and sessions. Then prepare inputs:

```sh
python scripts/ace_fastapi_sessions_demo.py \
  --mode dry-run \
  --model YOUR_CODEX_MODEL \
  --session-id CANONICAL_SESSION_ID \
  --output logs/ace-reflections/graphify-preview
```

Repeat `--session-id` for additional sessions. Default batch size is one. `--index`, `--project`, `--implementation`, and `--skill` select another indexed corpus or configuration. `--reasoning-effort`, `--batch-size`, `--timeout`, and `--max-input-chars` control inference settings and limits. Dry run records the model setting but does not load or invoke the SDK.

The example imports the selected skill plus associated context, hooks, and local resources; it keeps all nested skill files. MCP configuration is excluded from this example's selected scope. The generic reflector accepts any `MetaAgent` and supports MCP targets too. Nothing is installed or executed from the imported configuration.

Review `batch-001.json` before inference. It contains the exact prompt/schema, resource catalog, redacted analysis snapshot, hashes, and event inclusion/truncation report. Best-effort redaction is not a privacy guarantee; review sensitive source data yourself. An oversized request fails rather than silently discarding configuration files.

## Generate real reflections

After reviewing the preview, use the same session/model/configuration selection and a new output directory:

```sh
python scripts/ace_fastapi_sessions_demo.py \
  --mode reflect \
  --model YOUR_CODEX_MODEL \
  --session-id CANONICAL_SESSION_ID \
  --output logs/ace-reflections/graphify-live
```

This invokes the model and may incur usage charges. It does not apply curations or change Graphify. Outputs must be outside every indexed session source root, so reflections cannot accidentally enter their own evidence corpus. The script reads the existing index without syncing it.

Use `--resume` with the same live output directory and arguments to reuse completed batches. Reuse requires matching prompt/schema/input/configuration fingerprints and settings, and cached findings are revalidated. Failed/incomplete batches have no successful reflection artifact and are rerun. Changed selection or imported configuration requires a new run directory. Batch artifacts are not overwritten.

`--mode toy` explicitly selects the previous deterministic demo, which does apply toy curations in memory. It is separate from `dry-run` and `reflect`.

## Library API

```python
from thearc.learning import CodexReflector, CodexReflectorConfig

reflector = CodexReflector(
    CodexReflectorConfig(model="YOUR_CODEX_MODEL"),
    artifact_dir="logs/ace-reflections/library-run",
)
preview = reflector.prepare(session_bundles, meta_agent)  # Offline
reflection = reflector.reflect(session_bundles, meta_agent)  # Real SDK inspection
```

To use the shared flow, pass the reflector to `AcePipeline` and call `pipeline.run(meta_agent, query, reflection_only=True)`. A curator is not required in this mode. Custom flows and evaluators are rejected in reflection-only mode; the returned agent is an unchanged copy.

`Reflection.items` is a collection of individual assessments. Each `ReflectionItem` has a required `ResourceTarget`, a `ReflectionRating` enum (`helpful`, `neutral`, or `harmful`), a concrete `reason`, session/event evidence pairs, and limitations. Targets support skill instructions, nested files, context, hooks, MCPs, rules, workflows, commands, and uniquely resolvable Markdown sections. Prefer the smallest relevant section over rating an entire file.

The rating describes the configuration's contribution, not simply task success/failure. **Neutral means observed use had no meaningful positive or negative effect**; missing evidence or an unused resource is not neutral. Every item needs evidence, including neutral items. Omit resources that cannot be assessed and explain the gap in the envelope's limitations.

For example, an illustrative model response is:

```json
{
  "summary": "The graph-first instruction added unnecessary work in this session.",
  "items": [
    {
      "target": {"kind": "context", "name": "AGENTS.md", "section": "Graphify"},
      "rating": "harmful",
      "reason": "This section required a full graph rebuild for a spelling-only change, delaying the requested edit without useful information.",
      "evidence": [{"session_id": "session-1", "event_id": "event-7"}],
      "limitations": ["This assessment concerns only the supplied session."]
    }
  ],
  "limitations": []
}
```

The IDs and section above are illustrative; real responses must resolve to the supplied evidence/configuration. Historical ranks are withheld from the configuration snapshot and catalog. Known rank encodings in session text and arguments (structured `ranks`, YAML counters, and ranked Markdown headings) are also hidden to avoid bias from echoed configuration. Arbitrary prose about past ratings cannot be reliably scrubbed; the prompt explicitly tells the agent not to use or request historical ratings. Fresh ratings are structured output only: generating a reflection does not increment stored counts or mutate the `MetaAgent`.

The prompt and schema are version 2. Earlier category-based reflection artifacts are not reused by resume. Legacy summary fields remain available for older ACE consumers, but `items` is the authoritative structured assessment collection.

Schema validity is not factual correctness. Host checks reject missing targets, invalid ratings, blank reasons, absent/unknown evidence, wrong-session citations, unknown resources, and ambiguous headings. A reviewer must still decide whether the evidence supports the rating. An empty `items` list is valid when evidence is insufficient.

## Artifacts and execution boundary

A run contains a selection/settings manifest and a redacted imported snapshot. Each generated batch has:

- `input.json`: exact supplied evidence, prompt/schema, rank-free snapshot, and version/hash metadata.
- `reflection.json`: validated findings, canonical evidence IDs, host-assigned identity, SDK thread/turn IDs, and usage when available.
- `report.md`: findings grouped by resource, with evidence IDs and a link to the input.
- `run.jsonl`: lifecycle and retry/failure status, using the existing `RunJournal`.

Failures are explicit: missing/version-mismatched SDK, size-limit failure, timeout, transient SDK overload, incomplete response, invalid schema, invalid evidence/target, or isolation failure. Other SDK failures (including authentication failures not exposed as typed errors) are reported as `sdk_error`; they are not disguised as empty reflections. SDK exception text and hidden reasoning are not persisted. Retries default to zero and apply only to SDK-classified transient overload failures. A deadline closes the owned runtime; request-size limits are not a guaranteed spending cap.

The adapter uses a new ephemeral thread in a temporary directory, read-only sandboxing, denied escalation, disabled operational features/hooks/web search/plugins, suppressed skill/project instructions, and explicit disabling of inherited MCP servers before thread creation. Effective feature overrides are checked before proceeding. It also rejects unexpected tool activity in the collected result. These controls are version-pinned, not a general-purpose security boundary for arbitrary future runtimes; they do not replace OS sandboxing. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), [hook controls](https://learn.chatgpt.com/docs/hooks)

Current configuration is not presumed to have been installed or loaded during historical sessions. Event selection preserves the task, final assistant response, and whole known tool-call groups within the count budget; it is not yet a relevance-ranking system. Oversized groups can be omitted, and text/arguments can be truncated with explicit metadata. Only messages and tool calls/results are supplied; hidden reasoning and opaque raw events are excluded.

## Verification status

Offline tests cover all four native source adapters, mixed-source identities, strict output schemas, citations/targets, truncation/redaction, reflection-only flow, retries, and completed-batch reuse. A separate opt-in test starts the real pinned runtime and creates a thread but replaces the inference call:

```sh
python -m pytest -q
THEARC_TEST_CODEX_RUNTIME=1 python -m pytest \
  tests/test_codex_reflector.py::test_installed_runtime_isolation_without_inference -q
```

The local runtime probe and a dry run against a collected FastAPI session have passed. No live inference has been run for this implementation yet: model-side schema acceptance and reflection quality still need the opted-in smoke test. The full ten-prompt evaluation and actual configuration improvement are subsequent steps, not claimed results.
