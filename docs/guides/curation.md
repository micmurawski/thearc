# Curate changes

Curation turns reflections into an adapted MetaAgent. Unlike the reflector, the
curator sees historical ranks and assessment/change history. It reads configuration
resources and proposes addressed `ADD`, `EDIT`, or `REMOVE` operations with reasons
and evidence-backed reflection IDs.

## Curate saved findings

```sh
thearc curation run --agent artifacts/my-agent \
  --reflections artifacts/reflections \
  --backend YOUR_BACKEND --model YOUR_MODEL_ID \
  --output artifacts/curation --result-version 1.1.0
```

This invokes a runtime and commits validated changes. There is no `--execute`
gate or internal human-approval step. Review and approval can happen in your
application or pull-request workflow. It does not install configuration or create
a Git commit. `--result-version` is optional and represents an explicit release
label, not measured improvement.

In Python, using an existing `agent` and list of `reflections`:

```python
from thearc import MetaAgent
from thearc.learning.curation import create_curator, run_curation
from thearc.learning.reflection import ReflectorConfig

curator = create_curator("YOUR_BACKEND", ReflectorConfig(model="YOUR_MODEL_ID"))
epoch = run_curation(agent, reflections, curator, output="artifacts/curation")
adapted = MetaAgent.model_validate(epoch["agent"])
print(agent.diff(adapted))
```

## Inspect what changed and why

```sh
thearc curation show artifacts/curation
thearc curation diff artifacts/curation
thearc curation history artifacts/curation
```

`show` includes each change's target, operation, rationale, and reflection IDs.
Use those IDs to find the reflection and its session/event citations. `history`
shows committed epochs; `diff` compares the saved baseline and result. Add
`--include-ranks` to see assessment counters or `--no-version` to hide the release
label. These inspection commands do not invoke a runtime.

An epoch records its parent, configuration, assessments, changes, and provenance.
Candidate files are written in the curation workspace and imported back into
MetaAgent for validation. Failed attempts remain available for diagnosis without
publishing a successful epoch. A justified no-change result is valid.

## Boundaries

The default curator can edit prose in skills, Markdown references, context
documents, and rules. Hooks, MCP configuration, workflows, commands, and
non-Markdown bundled files are read-only. Models cannot directly rewrite historical
ranks or release identity. Assessment history supports deduplication and tracks
which configuration revision was assessed; ranks are not objective quality scores.

The curator uses scoped read/history/change tools rather than unrestricted
filesystem access. Custom Python runners remain trusted application code.
Secret detection is best effort, and diffs are not automatically redacted.

For another epoch, use the committed configuration and reflections appropriate
to that revision. Automatic release orchestration, rename/split/merge lineage,
and proof of improved performance are outside this workflow.
