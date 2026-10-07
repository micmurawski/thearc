# Generate reflections

`Reflection = Agent(PROMPT + EVIDENCE)`. The response belongs to the reflector;
the source session and the reflector's execution session are separate artifacts.
Use `thearc.resources.Reflector` for both execution modes.

## Submit reflections through tools

For large histories, attach `ReflectionTools` to the reflector. Resource content
is fetched on demand. The prompt lists what can be cited (resource IDs, or agent
files with section line ranges) but not their content, and no second copy of the
session is added. The caller supplies a schema for **one** reflection. The
`submit_reflections` tool accepts `{"reflections": [...]}` and the tool execution
path returns `list[Reflection]`. The accepted tool arguments are the output;
a final assistant message is not a submission.

```python
from thearc.resources import ReflectionTools, Reflector, TaggedReflection

# One reflection schema. Extend it with domain-specific fields if needed.
class ReflectionSchema(TaggedReflection):
    pass

tools = ReflectionTools(
    agent=agent,
    evidence=trajectory,
    reflection_schema=ReflectionSchema,
)
reflector = Reflector(implementation="codex", model="YOUR_MODEL_ID",
                      artifact_dir="artifacts/reflections")
reflections = reflector.handoff("Evaluate the agent's contribution to this task.",
                                trajectory, tools=tools)

# Accepted tags become evidence-checked assessments for the existing rank ledger.
candidate = curator.curate("Improve the agent from these findings.",
                           tools.assessments, agent=agent)
```

`TaggedReflection` has `reasoning` (a concise evidence-based explanation),
`error_identification`, `root_cause_analysis`, `correct_approach`, `key_insight`,
`event_ids`, and `bullet_tags`. The three error/approach fields may be null.
Each tag has a resource `id` and a `tag`: `helpful`, `neutral`, or `harmful`.
Custom Pydantic models and object JSON Schemas are also accepted. Automatic tag
assessments use the top-level `bullet_tags`, `event_ids`, and `reasoning` fields.
The typed `ResourceAssessmentReport` is also supported. Other custom outputs
remain observations, with no inferred ratings.

Tools available to the model:

| Tool | Behavior |
| --- | --- |
| `list_resources` | Search names and section titles; return paged IDs and targets, optionally with ranks. |
| `read_resource` | Read a bounded resource/section page; continue using `next_offset`. |
| `list_evidence` | Find event IDs by literal text search within the selected evidence, with short previews. |
| `submit_reflections` | Validate and accept the complete reflection list, or return errors for correction. |

Submission validates the schema, resource references against the frozen MetaAgent,
and tag citations against the supplied evidence. Errors identify the failing field.
There are three submission attempts by default (`max_submissions=`); invalid lists
are rejected in full. An explicit empty list is valid. Identical retries return the
same receipt, while changed submissions after acceptance are rejected. The tool
does not modify the agent or ranks. The curation ledger suppresses overlapping
evidence and repeated assessments when calculating ranks.
Each batch allows up to 100 reflections and 256,000 characters of tool arguments;
resource reads default to 8,000 characters and list pages to at most 50 entries.

## Cite agent files by line

`ReferencedReflection` rates the agent through its files instead of resource IDs.
The reflector reads the rank-free agent as ordinary files and cites line ranges:

```python
from thearc.resources import ReferencedReflection, ReflectionTools, Reflector

tools = ReflectionTools(agent=agent, evidence=trajectory, reflection_schema=ReferencedReflection)
reflections = reflector.handoff(prompt, trajectory, tools=tools)
# {"resource_refs": [{"ref": "skills/debug/SKILL.md#L14-L25", "tag": "harmful"}], ...}

ranked, ledger = agent.propagate_ranks(tools.assessments, epoch_id="run-1")
print(ranked.to_labeled_markdown(include_ranks=True))
```

`agent.files()` lays the agent out as `skills/<name>/SKILL.md`, the skill's bundled
files, `context/<file>`, `rules/`, `workflows/`, `commands/`, `hooks.json` and
`mcp.json` (one entry per hook or server). Files are rendered without rank metadata,
so line numbers belong to that configuration revision and ranks never shift them.

Ranks stay on resources and Markdown sections. Each citation is credited to the
innermost section containing it: `SKILL.md#L72-L85` inside "Step 1" rates "Step 1";
a range covering several whole sections rates their parent. A range that starts in
one section and ends in a sibling, falls outside the file, or names an unknown file
is returned as a tool error for the reflector to fix. The cited range is kept on the
assessment (`reference`, with the section's `target_id`) so a curator can see exactly
which lines were judged. The reflector's file outline shows each section's line range
and `SectionId`, and the submission receipt lists where every reference landed:

```json
{"ref": "skills/graphify/SKILL.md#L72-L85", "tag": "harmful",
 "section_id": "res_41467b7b090e1c311f393cfe", "section": "Step 1 - Ensure graphify is installed"}
```

`ranked.to_labeled_markdown(include_ranks=True)` then shows
`### Step 1 - Ensure graphify is installed [SectionId: res_41467b7b…, ranks: (harmful: 1, …)]`.

`ReferencedReflection` has no `event_ids`: the whole supplied history is the evidence
for each rating, and the overlap rule counts one vote per section per history. With
this schema the model gets these tools instead of the resource-ID tools:

| Tool | Behavior |
| --- | --- |
| `list_files` | Every agent file with its line count. |
| `read_file` | Numbered lines of one file; continue from `next_line`. |
| `submit_reflections` | Validate references and accept the complete list, or return errors. |

The instructions also list each file's section line ranges. `MetaAgent.propagate_ranks`
counts accepted assessments into the agent and returns the updated ledger; pass that
ledger to the next call so repeated reports are not counted twice. Inspect the same
view from the CLI:

```sh
thearc reflection resources --session-id 14a6b5fe --ranks              # Markdown, headings labelled
thearc reflection resources --session-id 14a6b5fe --format files --output agent-files
```

## Resource IDs

Resource IDs identify addresses within an evaluated agent snapshot. Whole resources
and unambiguous Markdown headings are supported; individual prose bullets are not
separate targets. Ranks do not affect IDs or the evaluated configuration revision.
`ResourceView(agent, ledger=ledger)` exposes the same IDs and optional current-content
and lifetime ranks through `list`, `read`, and `to_markdown(resource_id, include_ranks=True)`.
Without a ledger, only counters stored in the supplied agent are known.

The `tools=` integration currently uses the built-in Codex adapter. Select the
transport when constructing the reflector:

```python
reflector = Reflector(implementation="codex", model=model, tool_transport="mcp")
reflections = reflector.handoff(PROMPT, trajectory, tools=tools)
```

`tool_transport="mcp"` owns a temporary authenticated loopback MCP server backed by
the supplied `ReflectionTools`. Python schema validators, rank lookups, and correction
retries all use that same instance. The endpoint is stopped on success or failure.
It supports evidence prompts, imported trajectories, and native session/prefix forks.
The default `tool_transport="host"` uses scoped host tools and supports evidence
prompts and imports. The pinned native fork API cannot attach host tools; use MCP
for those forks.

For evidence prompts, compose the prompt yourself and pass the tools separately:

```python
tools = ReflectionTools(agent=agent, evidence=selected_events, reflection_schema=ReflectionSchema)
reflections = reflector.reflect(PROMPT + "\n\n" + evidence_to_markdown(selected_events), tools=tools)
```

## Attach the MCP server to a harness

Install the optional transport dependency:

```sh
python -m pip install -e '.[mcp]'
```

Configure your MCP client to launch this command, using absolute paths:

```sh
thearc reflection serve \
  --agent /path/to/evaluated-agent \
  --index /path/to/sessions.sqlite \
  --session-id SESSION_ID --trajectory 0 \
  --output /path/to/new-submission.json
```

The evaluated agent can be a thearc workspace or a MetaAgent JSON file. Alternatively,
`--evidence /path/to/events.json` accepts a JSON array of canonical events instead
of the index/session options. Optional `--schema` takes a JSON Schema for **one**
reflection; the default is `TaggedReflection`. `--ledger` supplies a saved
`AssessmentLedger` for historical ranks.

For a client using `mcpServers` configuration, the server entry looks like:

```json
{
  "mcpServers": {
    "thearc-reflection": {
      "command": "/path/to/thearc/.venv/bin/thearc",
      "args": ["reflection", "serve", "--agent", "/path/to/agent.json",
               "--evidence", "/path/to/events.json",
               "--output", "/path/to/new-submission.json"]
    }
  }
}
```

Client configuration syntax varies by harness. This stdio server is independent of
thearc's bundled execution adapters: your harness owns inference, history loading,
tool permissions, and stopping. Tell the reflector to finish with an accepted
`submit_reflections` call. No inference is performed by the MCP server itself.
One server process serves one reflection job; use a fresh output path for each job.

The evidence option pins the validation scope; it does not inject that evidence into
the harness conversation. Supply the matching session/trajectory to the harness.
Tool definitions and paged results contain no complete evidence dump.

Only a fully accepted batch creates the output file. It contains reflection artifacts,
the resource ID mapping, and curator assessment inputs. Publication is atomic and
refuses overwrite. Load the assessment inputs for curation as follows:

```python
import json
from pathlib import Path
from thearc.learning.curation.models import AssessmentReport

submission = json.loads(Path("new-submission.json").read_text())
assessments = [AssessmentReport.model_validate(item) for item in submission["assessments"]]
candidate = curator.curate("Improve the agent.", assessments, agent=agent)
```

## Evidence in the prompt

```python
from thearc.resources import Reflector, ResourceAssessmentReport

reflector = Reflector(implementation="codex", model="YOUR_MODEL_ID", artifact_dir="artifacts/reflections")
reflection = reflector.reflect(
    "Assess the configuration's contribution. Cite supplied event IDs.\n\n" + trajectory.to_markdown(),
    agent=agent, output_schema=ResourceAssessmentReport, validate_resources=True,
)
print(reflection.text)
```

The operator selects and formats evidence. `evidence_to_markdown(events)` accepts
selected event records or a JSON array. It adds no hidden selection or truncation.
Schemas may be Pydantic `ReflectionBaseModel` subclasses or JSON Schema dictionaries.
Without a schema, the output is text. See [schemas and validation](resources.md).

## Native handoff reflection

```python
reflection = reflector.handoff("Reflect on this task.", trajectory, agent=agent)
reflection = reflector.handoff("Reflect with earlier context.", session.through(trajectory), agent=agent)
```

The selected history supplies the evidence. Codex can fork a captured native prefix
or import supported normalized history into a new persisted session. The source is
unchanged. The response is `reflection.text`; the execution receipt identifies the
new session. Unsupported translations fail before inference.

## Preview and inspect

```sh
thearc reflection run --implementation codex --model YOUR_MODEL_ID \
  --prompt-file prompt-with-evidence.md --output artifacts/preview
thearc reflection handoff --implementation codex --model YOUR_MODEL_ID \
  --prompt-file reflection.md --source logs/claude-traning --session-id 14a6b5fe \
  --trajectory 0 --tools mcp --output artifacts/handoff-preview
thearc reflection show artifacts/reflections
```

Add `--execute` to run a previewed request in a new output directory. Optional
`--agent`, `--agent-mode`, `--schema`, and `--validate-resources` follow the Python
interface. `--session-id` accepts an indexed, native or collector run ID, or a unique
prefix; `--source` registers and syncs a directory first, and `--index` defaults to
`~/.thearc/learning.sqlite`. `--tools host|mcp` submits findings through
`ReflectionTools` (built-in Codex); a collector batch supplies the recorded agent
unless `--agent` is given. `--through` requires
`--trajectory` and includes earlier context. Preview does not probe the runtime
or guarantee its conversion capabilities.

For custom execution use `--implementation custom --runner package.module:function`.
The callback is imported only when execution starts. See [runtime contracts](../reference/runtimes.md).
