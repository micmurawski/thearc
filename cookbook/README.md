# Cookbook

Start with [resource_ace.py](resource_ace.py): selected immutable history → reflection
→ validated resource assessments → curation → CurationResult with a detached MetaAgent. Keep the result to merge parallel adaptations.
See [your first ACE run](../docs/start/first-ace.md) for setup and calls.

Other standalone examples:

- [graphify_evidence.py](graphify_evidence.py): filter indexed sessions, match behaviors, and reflect on matched evidence.
- [graphify_patterns.py](graphify_patterns.py): a retry matcher that retains intervening commentary.
- [graphify_handoff.py](graphify_handoff.py): inherit a trajectory, reflect with a custom schema, then curate.
- [graphify_parallel.py](graphify_parallel.py): adapt candidates concurrently and resolve their merge.
- [snapshot_sessions.py](snapshot_sessions.py): save portable filtered evidence.
- [inspect_evidence.py](inspect_evidence.py): bounded offline evidence retrieval.
- [prepare_handoff.py](prepare_handoff.py): save a portable task/context plan.
- [async_flow.py](async_flow.py): general asynchronous flow composition.

See [Graphify prompt-result examples](../docs/examples/graphify.md) for commands,
schema definitions, and loading the recorded agent baseline and captured sessions.
The evidence recipe's `adapt()` takes a `SessionStore`, filters, a pattern, a
configured reflector, and a curator. It has no separate CLI or input manifest.
Handoff and parallel recipes use `--inputs` for mixed Codex/Claude/Antigravity/Pi sessions. Use `--agent` for the
adaptation target, and `--implementation` for the execution runtime. Evidence and
parallel recipes support all bundled prompt runtimes and custom runners; historical
handoff currently uses the built-in Codex runtime with translated source history.

The portable plan is an offline export. Use `Reflector.handoff()` to continue
captured conversation history. The retired batch/toy recipes have been replaced by
explicit composition through `thearc.resources`; see the
[migration guide](../docs/guides/migration.md).
