"""Standalone session-batch reflection flow. No curation or configuration updates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from thearc.flow import Flow
from thearc.flow.decorators import node
from thearc.models.agent import MetaAgent

from .ace import AceConfig, HistorySessionMaterializer, SessionBundle
from .codex_reflector import CodexReflector, ReflectionError, redact
from .models import SearchFilters, Session
from .run import RunJournal
from .service import HistoryService


def _save(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def build_reflection_flow() -> Flow:
    """Select all scoped sessions -> assess evidence -> batch -> reflect -> report.

    Shared inputs are supplied by run_reflections(). The dry-run branch prepares
    actual prompts without producing placeholder Reflection objects.
    """

    @node(metadata={"stage": "select", "description": "Enumerate the entire source corpus"})
    def select_sessions(history: Any, source_ids: list[str]) -> dict:
        sessions = []
        while page := history.list_sessions(SearchFilters(source_ids=source_ids), limit=1000, offset=len(sessions)):
            sessions.extend(page)
        return {"sessions": sorted(sessions, key=lambda session: session.id)}

    @node(metadata={"stage": "prepare", "description": "Report unusable sessions and prepare bounded batches"})
    def prepare_batches(
        sessions: list[Any], history: Any, reflector: Any, agent: Any,
        batch_size: int, attempt_dir: Any,
    ) -> dict:
        attempt_dir = Path(attempt_dir)
        selection, eligible = [], []
        agent = agent if isinstance(agent, MetaAgent) else MetaAgent.model_validate(agent)
        materializer = HistorySessionMaterializer(history)
        limits = AceConfig(max_events_per_session=reflector.config.max_events_per_session,
                           max_text_chars_per_event=reflector.config.max_event_chars)
        for session in sessions:
            session = Session.model_validate(session)
            # Inspect the full trace for terminal errors before bounded materialization.
            events = list(history.iter_events(SearchFilters(session_ids=[session.id])))
            errors = []
            for event in events:
                payload = event.raw.get("payload", {})
                if isinstance(payload, dict) and payload.get("type") == "task_complete" and payload.get("error"):
                    error = payload["error"]
                    code = error.get("codex_error_info", "source_execution_error") if isinstance(error, dict) else None
                    errors.append(code if isinstance(code, str) else "source_execution_error")
            activity = [event for event in events if event.kind in {"tool_call", "tool_result"}
                        or (event.kind == "message" and event.role == "assistant" and event.text.strip())]
            entry = {"session_id": session.id, "source_id": session.source_id, "harness": session.harness,
                     "event_count": len(events), "activity_events": len(activity), "source_errors": errors,
                     "status": "eligible" if activity else "skipped",
                     "reason": None if activity else "no_assistant_or_tool_activity"}
            selection.append(entry)
            if activity:
                bundle = materializer.materialize(session, limits)
                if not any(e.kind in {"tool_call", "tool_result"} or
                           (e.kind == "message" and e.role == "assistant" and e.text.strip()) for e in bundle.events):
                    raise ValueError(
                        f"No assessable activity survived materialization for {session.id}; increase limits"
                    )
                eligible.append(bundle)
        _save(attempt_dir / "selection.json", selection)

        batches: list[list[SessionBundle]] = []
        current: list[SessionBundle] = []
        for bundle in eligible:
            candidate = [*current, bundle]
            try:
                reflector.prepare(candidate, agent)
                fits = len(candidate) <= batch_size
            except ReflectionError as exc:
                if exc.code != "input_too_large":
                    raise
                fits = False
            if current and not fits:
                batches.append(current)
                current = []
            if not current:
                reflector.prepare([bundle], agent)  # Oversized single sessions fail before any inference.
            current.append(bundle)
        if current:
            batches.append(current)
        for number, batch in enumerate(batches, 1):
            _save(attempt_dir / f"batch-{number:03d}.json", reflector.prepare(batch, agent))
        return {"selection": selection, "batches": batches}

    @node(metadata={"stage": "reflect", "description": "Generate structured resource ratings, without curation"})
    def generate(batches: list[Any], reflector: Any, agent: Any, execute: bool, journal: Any) -> dict:
        results = []
        agent = agent if isinstance(agent, MetaAgent) else MetaAgent.model_validate(agent)
        for number, batch in enumerate(batches, 1):
            batch = [SessionBundle.model_validate(item) for item in batch]
            ids = [bundle.session.id for bundle in batch]
            entry = {"batch": number, "session_ids": ids, "status": "prepared"}
            if execute:
                journal.record("reflections", "batch_started", batch=number, session_ids=ids)
                try:
                    reflection = reflector.reflect(batch, agent)
                    entry.update(status="completed", reflection_id=reflection.id, items=len(reflection.items),
                                 artifact=f"{reflection.id}/reflection.json")
                except ReflectionError as exc:
                    # Failed batches are explicit and do not prevent independent batches from completing.
                    entry.update(status="failed", error=exc.code)
                journal.record("reflections", "batch_finished", **entry)
            results.append(entry)
        return {"batch_results": results}

    @node(metadata={"stage": "report", "description": "Save corpus coverage and reflection artifact links"})
    def report(selection: list[dict], batch_results: list[dict], execute: bool, attempt_dir: Any) -> dict:
        attempt_dir = Path(attempt_dir)
        failed = sum(item["status"] == "failed" for item in batch_results)
        result = {
            "status": ("no_evidence" if not batch_results else
                       "partial_failure" if failed else ("completed" if execute else "prepared")),
            "selected_sessions": len(selection),
            "eligible_sessions": sum(item["status"] == "eligible" for item in selection),
            "skipped_sessions": sum(item["status"] == "skipped" for item in selection),
            "failed_batches": failed, "batches": batch_results,
        }
        _save(attempt_dir / "summary.json", result)
        lines = ["# Session reflections", "", f"Status: {result['status']}", "",
                 (f"Selected: {len(selection)}; eligible: {result['eligible_sessions']}; "
                  f"skipped: {result['skipped_sessions']}."), "", "## Batches", ""]
        for entry in batch_results:
            if entry["status"] == "completed":
                lines.append(f"- Batch {entry['batch']}: {entry['items']} items — "
                             f"[report](../{entry['reflection_id']}/report.md)")
            else:
                lines.append(f"- Batch {entry['batch']}: {entry['status']} {entry.get('error', '')}")
        lines.extend(["", "## Skipped sessions", ""])
        for entry in selection:
            if entry["status"] == "skipped":
                lines.append(f"- {entry['session_id']}: {entry['reason']}; source errors: {entry['source_errors']}")
        (attempt_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"result": result}

    select_sessions >> prepare_batches >> generate >> report
    return Flow(start=select_sessions)


def run_reflections(
    history: HistoryService, agent: MetaAgent, reflector: CodexReflector, output: str | Path, *,
    source_ids: list[str] | None = None, batch_size: int = 3, execute: bool = False, resume: bool = False,
) -> dict[str, Any]:
    """Run the standalone flow. Existing output is allowed only with explicit resume."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    sources = history.list_sources()
    selected_sources = sorted(set(source_ids if source_ids is not None else [source.id for source in sources]))
    if not selected_sources or set(selected_sources) - {source.id for source in sources}:
        raise ValueError("Select at least one registered history source")
    output = Path(output).resolve()
    if any(output == source.root or source.root in output.parents for source in sources):
        raise ValueError("Output must be outside the indexed source roots")
    output.mkdir(parents=True, exist_ok=resume)
    attempt_dir = output / f"attempt-{uuid4().hex}"
    attempt_dir.mkdir()
    reflector.artifact_dir = output
    reflector.resume = resume
    journal = RunJournal(attempt_dir / "run.jsonl")
    _save(attempt_dir / "manifest.json", {
        "source_ids": selected_sources, "batch_size": batch_size, "execute": execute,
        "settings": reflector.config.model_dump(), "history_revision": history.revision,
    })
    _save(attempt_dir / "agent.json", redact(agent.without_ranks().model_dump(mode="json")))
    shared = {"history": history, "agent": agent.without_ranks(), "reflector": reflector,
              "source_ids": selected_sources, "batch_size": batch_size, "execute": execute,
              "attempt_dir": attempt_dir, "journal": journal}
    journal.record("reflections", "flow_started")
    try:
        build_reflection_flow().run(shared)
    except BaseException as exc:
        journal.record("reflections", "flow_failed", error=getattr(exc, "code", type(exc).__name__))
        raise
    result = {**shared["result"], "output": str(output), "attempt_dir": str(attempt_dir)}
    journal.record("reflections", "flow_finished", **result)
    return result
