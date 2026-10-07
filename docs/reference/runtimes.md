# Runtime support and setup

These are the adapters implemented and tested by this repository—not a general
claim about every version or capability of the upstream products.

## Current runtime support

| Harness | Session ingestion and configuration installation | Bundled reflection/curation execution |
| --- | --- | --- |
| Antigravity | Supported | SDK adapter; offline contracts tested, live acceptance pending |
| Claude | Supported | Claude Code CLI adapter; offline contracts tested, live acceptance pending |
| Codex | Supported | SDK adapter; local mock-model persistence and isolation tests |
| Pi | Supported | Supply a custom runner; no bundled native execution adapter |
| OpenCode | Session ingestion supported | Supply a custom runner; no bundled native execution adapter |

Session format, execution backend, and installation target are independent.
Reflection and curation require explicit backend/model choices. Missing runtimes
fail rather than silently falling back to another provider. A successful experiment
does not establish general reflection quality or equivalent behavior across agents.

`thearc reflection serve` separately exposes reflection submission and paged resource
lookup over stdio MCP. Any compatible harness can attach it using its own MCP
configuration; this does not require a bundled execution adapter. Install
`thearc[mcp]` (MCP Python SDK 2.2–2.x). See
[tool-based reflection](../guides/reflections.md#submit-reflections-through-tools).
`Reflector(..., tool_transport="mcp")` manages its own temporary loopback MCP endpoint
for `reflect(..., tools=...)` and `handoff(..., tools=...)`, including native forks.
The default `tool_transport="host"` uses scoped Codex host tools; the pinned native
fork API does not support attaching host tools.

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
Codex authentication. Prompt reflection creates isolated ephemeral inspections. Handoff creates a new
persisted conversation using a native fork or supported history import. Runtime controls and supported model settings
are version-specific; arbitrary newer SDKs are not assumed compatible.

Native handoff reflection uses a separate persistent `thread/fork` path. It appends
only the reflection prompt to the fork and keeps the source unchanged. Select it
with `thearc reflection handoff`; see [native handoff reflection](../guides/reflections.md#native-handoff-reflection).
The bundled native fork adapter currently supports Codex. Other backends continue
to support reflection from supplied evidence.

## Implement the runner contract

`Reflector` and `Curator` accept `implementation="custom"`, a model, and a trusted
`runner(prompt, schema, config)` callable. It returns a dictionary containing
`status="completed"` and `final_response` text, optionally thread/turn IDs, usage,
and runtime version metadata. The host validates the result against the supplied
output schema. With no schema, reflection returns text.

In the CLI use `--implementation custom --runner package.module:function`.
Preview validates the address but does not import it. Custom callbacks own runtime
isolation and deadlines. The host cannot sandbox application Python code.
Curation requests addressed changes using `CurationReport`; the host validates and
applies them through its scoped editing engine.
