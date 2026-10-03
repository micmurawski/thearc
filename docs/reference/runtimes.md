# Runtime support and setup

These are the adapters implemented and tested by this repository—not a general
claim about every version or capability of the upstream products.

## Current runtime support

| Harness | Session ingestion and configuration installation | Bundled reflection/curation execution |
| --- | --- | --- |
| Antigravity | Supported | SDK adapter; offline contracts tested, live acceptance pending |
| Claude | Supported | Claude Code CLI adapter; offline contracts tested, live acceptance pending |
| Codex | Supported | SDK adapter; a Graphify live ACE run has been recorded |
| Pi | Supported | Supply a custom runner; no bundled native execution adapter |

Session format, execution backend, and installation target are independent.
Reflection and curation require explicit backend/model choices. Missing runtimes
fail rather than silently falling back to another provider. A successful experiment
does not establish general reflection quality or equivalent behavior across agents.

## Antigravity

```sh
python -m pip install -e '.[antigravity]'
```

The adapter pins `google-antigravity==0.1.19`. It uses the SDK and Gemini Developer
API, not `agy -p` or cached CLI subscription credentials. Configure `GEMINI_API_KEY`
securely outside source files. Usage may be billed separately from a CLI subscription.
The adapter uses isolated temporary state and constrained tools. Its synchronous
entry point can be called through `asyncio.to_thread` in async applications.

## Claude

The adapter requires an installed, authenticated **Claude Code CLI 2.1.178**.
It checks that version and required flags before execution; other versions are not
automatically accepted. There is no additional `thearc[claude]` Python extra.
The headless subprocess transport currently targets macOS/Linux.

It preserves normal CLI authentication while disabling inherited instructions,
skills, hooks, unrelated integrations, and operational tools for inspection.
Version/help probes do not establish live model or structured-output acceptance.

## Codex

```sh
python -m pip install -e '.[codex]'
```

The adapter pins `openai-codex==0.157.1` and uses its local runtime with existing
Codex authentication. It creates isolated ephemeral inspections rather than
resuming the source conversation. Runtime controls and supported model settings
are version-specific; arbitrary newer SDKs are not assumed compatible.

Native handoff reflection uses a separate persistent `thread/fork` path. It appends
only the reflection prompt to the fork and keeps the source unchanged. Select it
with `thearc reflection handoff`; see [native handoff reflection](../guides/reflections.md#native-handoff-reflection).
The bundled native fork adapter currently supports Codex. Other backends continue
to support reflection from supplied evidence.

## Implement the runner contract

Reflection and curation factories accept `backend="custom"` plus a trusted
`module:function` runner, `runtime`, and `adapter_version`. For example:

```sh
thearc reflection run --agent artifacts/my-agent \
  --index artifacts/sessions.sqlite --source-id experiment \
  --backend custom --runtime pi --runner my_package.adapter:run \
  --adapter-version 1 --model YOUR_MODEL_ID --output artifacts/custom-preview
```

A runner receives `(prompt, schema, config)` and returns an envelope containing
`status="completed"` and `final_response`, a JSON string matching the schema.
It must invoke the chosen runtime after prompting, honor model/timeout controls,
and report honest completion and provenance. Optional metadata includes thread,
turn, usage, and SDK version. Preview does not import the custom callable.

Reflection citations use `{"event_id": "<supplied event ID>"}`. The host resolves
session IDs for saved findings. Evidence defaults to the compact view; select
`--evidence-view detailed` for more context. Both views exclude source harness
metadata. See [evidence views](../guides/reflections.md#evidence-views).

Reflection expects findings. Custom curation uses a structured host-tool loop:
each response requests a scoped tool or finishes. It is not the reflection schema.
Bundled Claude/Antigravity curators also use this loop; Codex uses SDK dynamic tools.

Python callbacks are trusted code. The shared engine validates responses but cannot
sandbox arbitrary callbacks or force a blocked callback to respect a timeout.
Enforce isolation in the adapter, disable historical command/configuration
execution, and capability-test each runtime before production use.
