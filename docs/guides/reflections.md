# Generate reflections

A reflection is a collection of assessments of specific configuration parts.
Each item identifies a resource or section, rates it `helpful`, `neutral`, or
`harmful`, explains why, and cites delivered session/event IDs.

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
