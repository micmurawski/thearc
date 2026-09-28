"""Explicit, sequential ACE graph used by the Graphify cookbook recipe.

Batch nodes expose one progress item per session/reflector call. All reflections
describe the same baseline; curation commits once, after every batch succeeds.
"""

from pathlib import Path
from typing import Any

from thearc import MetaAgent
from thearc.flow import Flow
from thearc.flow.decorators import node
from thearc.flow.viz import FlowTracker
from thearc.learning.ace.pipeline import (
    AceConfig,
    AceRunResult,
    Curation,
    Reflection,
    SessionBundle,
    UpdateResult,
    chunks,
)
from thearc.learning.curation import run_curation
from thearc.learning.evidence.snapshot import write_json_exclusive


def build_flow(*, preview: bool = False) -> Flow:
    """Compose public flow nodes; preview has no curation node at all."""

    @node(metadata={"description": "Freeze the selected sessions before batching"})
    def select_sessions(sessions: list[Any]) -> dict:
        if not sessions:
            raise ValueError("ACE requires at least one session")
        return {"session_items": [{"session": session} for session in sessions]}

    @node(batch=True, items_key="session_items", results_key="bundles",
          metadata={"description": "One bounded evidence bundle per captured session"})
    def load_session(session: Any, materializer: Any, config: Any) -> Any:
        return materializer.materialize(session, config)

    @node(metadata={"description": "At most N sessions per reflection; retain the final partial batch"})
    def group_batches(bundles: list[Any], config: Any, output: Any, log: Any) -> dict:
        batches = [{"number": i, "batch": list(batch)}
                   for i, batch in enumerate(chunks(bundles, config.sessions_per_reflection), 1)]
        write_json_exclusive(Path(output) / "batches.json", {
            "sessions_per_reflection": config.sessions_per_reflection,
            "batches": [{"number": item["number"], "session_ids": [
                SessionBundle.model_validate(bundle).session.id for bundle in item["batch"]
            ]} for item in batches],
        })
        log.info(f"{len(bundles)} sessions → {len(batches)} sequential reflection batches")
        return {"reflection_batches": batches}

    @node(batch=True, items_key="reflection_batches", results_key="findings",
          metadata={"description": "Save input previews; no inference" if preview else
                    "Reflect on each batch against the same rank-free baseline"})
    def inspect_batch(number: int, batch: list[Any], reflector: Any, agent: Any, output: Any, log: Any) -> Any:
        bundles = [SessionBundle.model_validate(item) for item in batch]
        baseline = MetaAgent.model_validate(agent).without_ranks()
        if preview:
            write_json_exclusive(Path(output) / f"batch-{number:03d}.json", reflector.prepare(bundles, baseline))
            result = {"number": number, "session_ids": [bundle.session.id for bundle in bundles]}
        else:
            result = reflector.reflect(bundles, baseline)
        log.info(f"{'Prepared' if preview else 'Reflected'} batch {number}: {len(bundles)} sessions")
        return result

    @node(metadata={"description": "Preview complete; no model calls or configuration commit"})
    def finish_preview(findings: list[Any], output: Any) -> dict:
        write_json_exclusive(Path(output) / "preview.json", {"status": "preview_only", "batches": findings})
        return {}

    @node(metadata={"description": "Aggregate all findings; edit, validate and commit one MetaAgent epoch"})
    def curate_epoch(agent: Any, findings: list[Any], curator: Any, output: Any,
                     run_id: str, result_version: str, sessions: list[Any], log: Any) -> dict:
        epoch = run_curation(
            MetaAgent.model_validate(agent), [Reflection.model_validate(item) for item in findings], curator,
            output=Path(output) / "curation", epoch_id=run_id, result_version=result_version,
        )
        result = AceRunResult(
            run_id=run_id, epoch_id=epoch["epoch_id"], sessions=sessions, reflections=findings,
            curations=[Curation.model_validate(change) for change in epoch["changes"]],
            update=UpdateResult(agent=MetaAgent.model_validate(epoch["agent"]),
                                applied_curation_ids=[change["id"] for change in epoch["changes"]]),
        )
        log.info(f"Committed {len(result.curations)} changes in {result.epoch_id}")
        return {"result": result}

    select_sessions >> load_session >> group_batches >> inspect_batch
    inspect_batch >> (finish_preview if preview else curate_epoch)
    return Flow(start=select_sessions)


def run_flow(
    *, agent: MetaAgent, sessions: list, materializer: Any, reflector: Any, curator: Any,
    config: AceConfig, output: Path, result_version: str, preview: bool = False,
    run_id: str = "graphify-epoch-001",
) -> AceRunResult | None:
    """Run in an existing, task-owned output directory; always retain flow.html.

    Callers choose the runtime (synthetic or live). Nothing is installed into the
    original project. A failed batch stops before curation; saved artifacts remain.
    """
    tracker = FlowTracker(build_flow(preview=preview), output=str(output / "flow.html"), refresh_interval=1)
    shared = {"agent": agent, "sessions": sessions, "materializer": materializer, "reflector": reflector,
              "curator": curator, "config": config, "output": output,
              "result_version": result_version, "run_id": run_id}
    tracker.run(shared)
    return None if preview else AceRunResult.model_validate(shared["result"])
