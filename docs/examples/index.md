# More examples

Start with [your first ACE run](../start/first-ace.md). For individual operations,
the repository contains runnable modules under `cookbook/`:

| Module | Purpose | Inference |
| --- | --- | --- |
| `cookbook.snapshot_sessions` | Save evidence from explicitly selected indexed sessions | None |
| `cookbook.inspect_evidence` | Search/read evidence with audit and budgets | None |
| `cookbook.reflect_from_evidence` | Preview or reflect on a saved snapshot | With `--execute` |
| `cookbook.prepare_handoff` | Save an offline task/evidence handoff | None |
| `cookbook.reflect_from_handoff` | Reflect on evidence referenced by a handoff | With `--execute` |
| `cookbook.reflect_with_backend` | Use a trusted custom runtime adapter | With `--execute` |
| `cookbook.async_flow` | Synthetic concurrent tasks with a browser visualization | None |
| `cookbook.graphify_ace` | Batched, visualized Graphify adaptation | With `--mode live` |

Run from the repository root and inspect the recipe's options first:

```sh
python -m cookbook.snapshot_sessions --help
python -m cookbook.reflect_from_evidence --help
python -m cookbook.graphify_ace --help
```

The [cookbook instructions](https://github.com/micmurawski/thearc/blob/HEAD/cookbook/README.md)
contain exact invocations. Some older convenience recipes default to Codex; pass
backend/model options explicitly where provided. The library factories and the
reflection/curation CLI have no default execution provider.

## Graphify experiments

Graphify recipes use the FastAPI submodule and locally captured session artifacts.
Those logs may not exist in a fresh clone. Preview generates inputs without model
calls. Offline mode uses synthetic findings/edits to test infrastructure. Only live
mode invokes real reflection and curation; none proves improved performance by itself.

The flow recipe batches sessions sequentially, then performs one curation step.
It writes a visualization, batch membership, reflections, and committed history.
Batch size is not a concurrency setting. It does not provide automatic recovery
of the whole run, installation, or held-out evaluation.

To collect fresh test sessions, follow the repository's
[prompt collection instructions](https://github.com/micmurawski/thearc/blob/HEAD/prompts/README.md).
The collector uses retained Git worktrees rather than editing the source checkout.
This creates real agent activity and can consume quota; it is separate from
reflecting on existing logs.
