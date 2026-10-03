# Generate reflections

## Native handoff reflection

Native handoff continues a fork of an existing conversation:
`S1R = AR(fork(S1) + RP)`. The session itself is the evidence. The reflector
receives only the appended reflection prompt; no separate evidence payload is added.

```sh
thearc reflection handoff --backend codex --session-id NATIVE_SESSION_ID \
  --model YOUR_REFLECTOR_MODEL --prompt-file reflection-prompt.txt \
  --output artifacts/native-reflection --execute
```

Omit `--execute` to save a prompt preview. Omit `--prompt-file` to use the default
reflection prompt. `thearc handoff reflect` is an alias. Use native runtime IDs;
`NativeSessionRef.from_session(indexed_session)` resolves them from indexed sessions.

```python
from thearc.learning.reflection import HandoffReflectorConfig, NativeSessionRef, create_handoff_reflector

reflector = create_handoff_reflector(
    "codex", HandoffReflectorConfig(model="YOUR_REFLECTOR_MODEL"),
    artifact_dir="artifacts/native-reflections",
)
result = reflector.reflect(NativeSessionRef(runtime="codex", session_id="NATIVE_SESSION_ID"))
print(result.fork.session_id)  # New persistent conversation, including its inherited history.
print(result.reflection)
```

To reflect on one prompt's trajectory, use
`NativeSessionRef.from_trajectory(session, trajectory)` after
[splitting the indexed session](sessions.md#split-a-session-into-trajectories).
This forks through the selected native turn, preserving earlier context and
excluding later prompts. Supply a prompt that focuses on the most recent request
and its response. The resulting reflector trajectory contains the final reflection.

The source remains unchanged. The selected model may differ from the original
model. Native handoff currently supports Codex; other runtimes require their own
native fork adapters. Operational tools are disabled for reflection. Native history
retains its original content and compaction behavior. The source workspace must exist.

Artifacts include the exact appended prompt, a fork receipt saved before inference,
the final text, a `handoff.json` result, and a lifecycle journal. Read results with
`thearc reflection show artifacts/native-reflection`. The native runtime stores the
continued session; `handoff.json` records its identity. Native reflection returns
text and session lineage, and does not fabricate indexed event IDs or ACE ratings.

## Reflection from supplied evidence

A reflection is a collection of assessments of specific configuration parts.
Each item identifies a resource or section, rates it `helpful`, `neutral`, or
`harmful`, explains why, and cites delivered events. The agent returns only
`event_id`; the host resolves session IDs when saving the findings.

The reflector sees the configuration without historical ranks. Its runtime must
inspect the supplied evidence **after receiving the reflection prompt** and produce
structured findings. Ratings are not precomputed from tool exit codes or old scores.
Unused or unassessable resources should be omitted, not automatically rated neutral.

## Preview before execution

```python
from pathlib import Path

from thearc import MetaAgent
from thearc.learning import EvidenceSnapshot
from thearc.learning.reflection import ReflectorConfig, create_reflector

agent = MetaAgent.from_workspace("artifacts/my-agent")
evidence = EvidenceSnapshot.load("artifacts/evidence")
reflector = create_reflector("YOUR_BACKEND", ReflectorConfig(model="YOUR_MODEL_ID"))
reflector.artifact_dir = Path("artifacts/reflections")

prepared = reflector.prepare_snapshot(evidence, agent)  # No inference.
reflection = reflector.reflect_snapshot(evidence, agent)  # Calls the runtime.
```

Select and authenticate an explicit [backend](../reference/runtimes.md). Previewing
does not validate your account's model availability. Reflection does not mutate
the MetaAgent or install its configuration.

## CLI

To prepare batches from an indexed source:

```sh
thearc reflection run --agent artifacts/my-agent \
  --index artifacts/sessions.sqlite --source-id experiment \
  --backend YOUR_BACKEND --model YOUR_MODEL_ID \
  --batch-size 2 --output artifacts/reflection-preview
```

This previews by default. Add `--execute` with a new output directory to run live.
`--resume --execute` can reuse compatible completed artifacts. Reuse checks include
input, configuration, model/settings, and adapter identity/version; it is not
native conversation continuation.

`--batch-size` controls sessions per reflection, not parallel model calls.
Input limits may split batches. Incomplete/no-evidence runs return nonzero.

## Read findings

```sh
thearc reflection show artifacts/reflections
```

Each reflection artifact directory contains `input.json`, `reflection.json`,
`report.md`, and `run.jsonl`. Read `items` in `reflection.json` for target-level
ratings and citations. Input and run artifacts help distinguish model findings
from execution failures; preview artifacts do not contain generated findings.

An empty item list is valid when evidence is insufficient. Citations establish
that evidence was supplied, not that the model's causal explanation is correct.
Model input is bounded and inline; omissions are reported. `reflect_snapshot()`
treats its snapshot as one batch, so do not assume a huge snapshot fits.

To change configuration using the findings, continue with [curation](curation.md).

## Evidence views

Reflection is `Agent(PROMPT + EVIDENCE)`. The prompt describes the assessment;
the evidence supplies configuration content and recorded interactions. Choose
the evidence representation with `ReflectorConfig(evidence_view="compact")`
or `thearc reflection run --evidence-view compact ...`.

- **`compact` (default):** keeps event IDs, roles, message/tool content, status,
  and truncation flags. Sessions remain separate groups, with included, omitted,
  and truncated event counts. Tool results refer to their calls through
  `call_event_ids`. Multiple runs within a session get local actor labels.
- **`detailed`:** adds session/source/run/call IDs, timestamps, normalized action
  kinds, resource hashes, and exact omitted/truncated event ID lists. It uses the
  same selected events and content limits as compact mode.

Both views exclude source `harness` metadata. Recorded text and configuration
content are retained, so they may still mention a provider or provider-specific
tools. Detailed mode does not retrieve omitted events or restore truncated text.

The compact evidence shape is illustrated below (IDs and content are examples):

```json
{
  "configuration_provenance": "current snapshot; historical installation/loading is unknown",
  "resources": [
    {
      "target": {"kind": "context", "name": "AGENTS.md", "section": null},
      "content": {"filename": "AGENTS.md", "content": "Run relevant tests.", "description": ""}
    }
  ],
  "sessions": [
    {
      "events": [
        {"id": "event-a", "kind": "tool_call", "role": "assistant", "tool_name": "shell",
         "arguments_json": "{\"command\":\"pytest\"}"},
        {"id": "event-b", "kind": "tool_result", "role": "tool", "status": "success",
         "text": "3 passed", "call_event_ids": ["event-a"]}
      ],
      "coverage": {"included_events": 2, "omitted_events": 0, "truncated_events": 0}
    }
  ],
  "selection_policy": "Prioritize the first user message and last assistant message, then take whole call groups in supplied order up to the event limit; preserve supplied order. Earlier filtering or limits may also have omitted events."
}
```

In `input.json`, `evidence` is the selected model-facing view; `evidence_audit`
retains sanitized provenance and exact omission lists for host lookup. Only the
selected view is appended to the prompt. Indexed session/event models are unchanged.

Custom reflection runners must now return citations as `{"event_id": "..."}`.
Saved `Reflection` objects still contain resolved `session_id`/`event_id` pairs,
so downstream curation can use the existing artifact format. Prompt/schema
versions and evidence-view settings prevent reuse of incompatible cached runs.
