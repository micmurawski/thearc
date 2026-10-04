# Curate changes

`Curator` consumes reflection responses and proposes changes to a detached MetaAgent.

```python
from thearc.resources import Curator

curator = Curator(implementation="codex", model="YOUR_MODEL_ID")
result = curator.curate("Improve the instructions using the findings.", [reflection], agent=agent,
                        output="artifacts/curation")
print(agent.diff(result.agent))
```

`CurationReport` is the default schema. Custom `CurationBaseModel` subclasses or
JSON schemas need an explicit adapter to `CurationReport`. Changes are validated
against the candidate in order. The input configuration stays unchanged.

Text reflections can support edits without creating ratings. To propagate ranks,
request `ResourceAssessmentReport` and call `reflection.assessments(history)` to
validate citations before passing the report to the curator. The report must refer
to the evaluated configuration revision.

Curation preserves assessment deduplication, resource restrictions, candidate
replay checks, workspace validation, and transactional epoch commits. It edits prose
resources; executable hooks, integrations, and scripts are excluded. Omitting
`output` returns a detached result. Supplying it saves a committed epoch and
failure artifacts. No-change results are valid.

## Saved reflections and epochs

```sh
thearc curation run --implementation codex --model YOUR_MODEL_ID \
  --agent my-agent --reflections artifacts/reflections --prompt-file curate.md \
  --output artifacts/curation
thearc curation show artifacts/curation
thearc curation history artifacts/curation
thearc curation diff artifacts/curation
```

The CLI accepts current `Reflection` artifacts. Reloading a result does not
reconstruct its source history or automatically convert typed findings into rank
assessments. Use the Python assessment adapter for rank updates.

See the [resource guide](resources.md#curate-a-revised-metaagent) for schemas,
resource validation, and bounded evidence retrieval during Codex curation.
