# Captured logs to Codex reflections through MCP

`cookbook/codex_example.py` shows the complete pipeline:

1. Register a transcript directory or collector batch with `Sessions` (harness detected).
2. Resolve one session by any ID and select a trajectory.
3. Call `Reflector.handoff()` with MCP tools; it translates the trajectory into
   Codex history and returns the accepted list of reflections.

## Run it

Install the `codex` and `mcp` extras and authenticate the installed Codex runtime
as described in [runtime setup](../reference/runtimes.md). Run from the repository root:

```bash
python -m cookbook.codex_example \
  --source logs/claude-traning \
  --session 14a6b5fe \
  --model YOUR_MODEL_ID \
  --output artifacts/codex-example
```

`--source` is any transcript directory or a collector batch; the default is
`logs/claude-traning`. Reflection tools need the agent configuration recorded before
the run, so the source must be a collector batch with `runs/*/manifest.json` and
`runs/*/agent-before.json` beside its `sessions/` files. `logs/pi-traning` and
`logs/opencode-traning` work the same way.

`--session` accepts an indexed ID, the native session ID, the collector run directory
name, or a unique prefix of any of them; the default is the most recent session.
`--trajectory` selects the prompt within it (default 0). Sources are indexed into
the shared `~/.thearc/learning.sqlite` (override with `--index`); re-running only
syncs changed files. Use a new output directory for each attempt; existing artifacts
are never overwritten. `--timeout` bounds startup and inference (default 300 seconds).

To save exactly what Codex would receive, without inference:

```bash
python -m cookbook.codex_example --session 14a6b5fe --prepare-only \
  --output artifacts/codex-preview
```

This writes `trajectory.codex.jsonl`, `trajectory.md`, `conversion.json` (the list of
transformations applied), `agent-files/` (what the reflector would read and cite), and
`agent.md` (the agent with section IDs and current ranks on every heading).
The same views are available from the CLI:

```bash
thearc sessions add logs/claude-traning
thearc sessions render 14a6b5fe --trajectory 0                 # Markdown
thearc sessions render 14a6b5fe --trajectory 0 --format codex  # Codex JSONL
```

## Indexing and translation

Each harness adapter normalizes native records into canonical events when a source
is synced. Translation happens only at handoff: `codex_items(...)` maps the selected
canonical events to Responses items, including correlated tool calls and results.
The shared Codex adapter creates a new persisted session, injects those items,
verifies the saved history, and appends the reflection prompt. The source session
remains unchanged.

```python
from thearc.resources import ReflectionTools, Reflector, Sessions

with Sessions.open() as sessions:
    sessions.add("logs/claude-traning")
    session = sessions.get("14a6b5fe")
    agent = sessions.agent(session)
history = session.trajectories[0]

tools = ReflectionTools(agent=agent, evidence=history, reflection_schema=ReflectionSchema,
                        output=output / "submission.json")
reflector = Reflector(implementation="codex", model=model, tool_transport="mcp",
                      artifact_dir=output / "reflection-runs")
reflections = reflector.handoff(PROMPT, history, tools=tools)
```

`Reflector` owns translation, runtime startup, MCP lifecycle, and result collection.
The cookbook only selects inputs and saves the outputs.

Foreign thinking records become labeled assistant text. They cannot be replayed
as signed native Codex reasoning items; empty thinking records are explicitly
marked as having no readable text.

Harness metadata and attachments are **not replayed**. Conversion skips them and
records their IDs in the run manifest (`conversion.lifecycle_event_ids`); they stay
in the canonical history, which is the evidence for every rating. Attachments can
contain instructions or hook output, so this limits what the reflector can infer
about resource use. The prompt requires it to report such uncertainty. Unsupported
conversation records or incomplete tool call/result pairs stop the handoff.

## Schema and MCP validation

`ReflectionSchema` defines **one reflection**. It extends `ReferencedReflection` with
`limitations`. Each reflection contains an evidence-based explanation, error and
correction fields, a key insight, and `resource_refs`: agent lines such as
`skills/debug/SKILL.md#L14-L25` with a `helpful`, `neutral` or `harmful` tag. Error
fields can be null; `resource_refs` can be empty when attribution is uncertain. The
whole trajectory is the evidence, so no event IDs are cited.

`Reflector` starts a temporary authenticated MCP endpoint on loopback with three tools:

- `list_files`: the recorded agent's files with their line counts.
- `read_file`: numbered lines of one file.
- `submit_reflections`: submit `{"reflections": [...]}` for validation.

The server validates the complete list against the single-reflection schema and
resolves every reference to the innermost resource or section containing it. Out of
range lines, unknown files and ranges crossing a section boundary produce tool errors
so Codex can correct the submission in the same conversation. Only an accepted
submission produces the result file; final assistant prose is not parsed as a
reflection. An accepted empty list is allowed when no finding is supported.

After acceptance the cookbook counts the votes with `agent.propagate_ranks(...)` and
writes the ranked agent, its ledger, and `ranked-agent.md`, where every section
shows its `SectionId` and ranks. The submission receipt reports the section each
reference landed in (`ranked`), and each ledger vote keeps `reference` and `target_id`.

The endpoint uses the original `ReflectionTools` instance, including Python schema
validators and its rank ledger. It is stopped when the run ends. MCP connection
details and credentials are private to the invocation.

The trajectory is imported once as history. The prompt does not duplicate it; it
lists the agent's files and section line ranges, and the MCP server supplies the
file content. Other
runtime tools and inherited MCP servers remain disabled for this invocation.

## Output files

The main result is **`<output>/reflections.json`**, a JSON array of validated
`ReflectionSchema` objects. Supporting files make the run inspectable:

| File | Contents |
| --- | --- |
| `reflection-runs/<run-id>/request.json` | Prompt, schema, and manifest including conversion details |
| `reflection-runs/<run-id>/agent-files/` | The exact agent files the reflector read and cited |
| `reflection-runs/<run-id>/history.json` | Canonical events handed off |
| `reflection-runs/<run-id>/destination.json` | New Codex session locator |
| `reflection-runs/<run-id>/response.json` | Runtime outcome and MCP call summary |
| `reflection-runs/<run-id>/<reflection-id>/reflection.json` | Reflection artifact with provenance |
| `submission.json` | Accepted Reflection artifacts and resource assessments for curation |
| `ranked-agent.json` | Recorded agent with the accepted votes counted into its ranks |
| `ranked-agent.md` | That agent as Markdown; sections show `[SectionId: res_…, ranks: (...)]` |
| `ledger.json` | Assessment ledger, including each vote's cited `reference`; pass it to later runs |
| `failure.json` | Error type and code if generation fails |

The Python entry points are `load(source, session, trajectory=...)`, which returns
the trajectory and its recorded agent, and `generate_reflections(history, agent, ...)`.
The latter returns `list[Reflection]`, with a `ReflectionSchema` in each `.output`,
and writes those outputs to `reflections.json`.
