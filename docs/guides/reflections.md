# Generate reflections

`Reflection = Agent(PROMPT + EVIDENCE)`. The response belongs to the reflector;
the source session and the reflector's execution session are separate artifacts.
Use `thearc.resources.Reflector` for both execution modes.

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
  --prompt-file reflection.md --index sessions.sqlite --session-id SESSION_ID \
  --trajectory 0 --output artifacts/handoff-preview
thearc reflection show artifacts/reflections
```

Add `--execute` to run a previewed request in a new output directory. Optional
`--agent`, `--agent-mode`, `--schema`, and `--validate-resources` follow the Python
interface. Handoff IDs are canonical indexed session IDs. `--through` requires
`--trajectory` and includes earlier context. Preview does not probe the runtime
or guarantee its conversion capabilities.

For custom execution use `--implementation custom --runner package.module:function`.
The callback is imported only when execution starts. See [runtime contracts](../reference/runtimes.md).
