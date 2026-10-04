# Cookbook

Start with [resource_ace.py](resource_ace.py): selected immutable history → reflection
→ validated resource assessments → curation → CurationResult with a detached MetaAgent. Keep the result to merge parallel adaptations.
See [your first ACE run](../docs/start/first-ace.md) for setup and calls.

Other standalone examples:

- [snapshot_sessions.py](snapshot_sessions.py): save portable filtered evidence.
- [inspect_evidence.py](inspect_evidence.py): bounded offline evidence retrieval.
- [prepare_handoff.py](prepare_handoff.py): save a portable task/context plan.
- [async_flow.py](async_flow.py): general asynchronous flow composition.

The portable plan is an offline export. Use `Reflector.handoff()` to continue
captured conversation history. The retired batch/toy recipes have been replaced by
explicit composition through `thearc.resources`; see the
[migration guide](../docs/guides/migration.md).
