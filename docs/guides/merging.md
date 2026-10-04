# Merge adaptations

Keep the full `CurationResult` from each adaptation pipeline. Its `.agent` contains
the candidate configuration; the rest of the result records the baseline, operations,
reflections, and assessment ledger needed to merge it reliably.

## Merge independent branches

```python
from thearc.resources import Curator, Merger

# Each pipeline must start from this same baseline.
adaptation_a = pipeline_a(baseline)
adaptation_b = pipeline_b(baseline)

resolver = Curator(implementation="codex", model="YOUR_MODEL_ID")
merged = Merger(resolver=resolver).merge(
    base=baseline,
    candidates=[adaptation_a, adaptation_b],
    prompt="Preserve supported improvements. Resolve contradictions using their evidence.",
    output="artifacts/merge",
)

if merged.unresolved:
    print([issue.model_dump() for issue in merged.unresolved])
else:
    print(baseline.diff(merged.agent))
```

`cookbook.resource_ace.adapt()` now returns a `CurationResult`, so its results can
be passed directly to this step. Existing callers obtain the configuration through
`result.agent`.

All inputs must share the supplied baseline, including its version and ranks.
Candidates must preserve the baseline's release identity; assign a release version
after merging. Each input's recorded edits are replayed and compared with its candidate
before any resolver call. Bare MetaAgents and different-baseline branches are rejected.

## What merges automatically

- A resource changed by only one branch.
- Identical changes, keeping their contributing provenance.
- Independent dictionary fields and resources.
- Non-overlapping line changes within text, including separate Markdown sections.

Different edits to the same text, differing additions to the same address, and
removal-versus-edit conflicts require a decision. Deleting a skill conflicts with
editing any file within it. Lists are treated as complete values; list entries are
not silently reordered or combined. Text merging is conservative about insertions
at the same boundary and ambiguous overlaps.

## Conflict resolution and review

The resolver uses the supplied Curator's implementation, model, settings, and runner.
It receives baseline content, conflicting proposals, source changes and rationales,
and supporting reflection records. The host embeds and validates the `MergeReport`
schema. There is one decision for each conflict:

| Action | Meaning |
| --- | --- |
| `select` | Choose exactly one supplied proposal ID in `selected_change_ids`. |
| `combine` | Supply addressed curation operations within that conflict's JSON path. |
| `retain_base` | Discard the competing proposals for that conflict. |
| `unresolved` | Leave a visible conflict and block a ready result. |

Proposal IDs are namespaced by conflict and source epoch. They differ from the
original curation change IDs. Conflict proposals identify their source epoch; the
source changes and reflection records are retained in the request artifact.

Combined operations and the final merged configuration pass the existing curation
checks: known reflection citations, prose-only changes, host-owned ranks, protection
of executable files, and workspace round trips. A resolver cannot use a conflict to
edit unrelated content. Invalid output raises an error and saves a failure record
when `output` is supplied.

After structural resolution, the resolver checks the combined guidance for contradictions
across resources using `MergeReview`. Any reported issues appear in `unresolved` and
block `.agent`. Revise the proposals or resolve the underlying guidance before rerunning.
This review is a model judgment, not a performance evaluation.

With `Merger()` and no resolver, there are no model calls: independent changes merge,
and conflicts remain explicit. You can provide a complete `MergeReport` through
`resolutions=`. With a resolver, `review=False` explicitly skips the final consistency
review; provenance records whether a review occurred.

## Results, ranks, and persistence

`MergeResult.agent` returns a detached MetaAgent and raises when conflicts remain.
`changes` contains net addressed operations. Each change records contributing source
change IDs and resolution IDs in `raw`. Rejected alternatives remain visible in
`provenance`, without being cited as support for selected changes.

Assessment records and imported baseline counts are deduplicated rather than adding
branch counters. Overlap decisions are recomputed in stable assessment-ID order using
the existing evidence-overlap policy. Suppressed observations remain in the ledger.
Old-content votes remain tied to their resource revision; they do not automatically
become votes for synthesized guidance.

`output` must be a new directory. It contains the full request, resolutions, optional
resolver execution artifacts, and an integrity-checked `merge.json`. Unresolved results
are also saved, with no ready agent. Load a result without inference:

```python
from thearc.resources import MergeResult

merged = MergeResult.load("artifacts/merge")
```

Without `output`, the result stays in memory; an explicitly configured resolver
artifact directory is still honored. A merge records multiple source epochs in its
own artifact. It does not advance a branch's single-parent curation HEAD or install
configuration. Evaluate the combined candidate before deploying it.
