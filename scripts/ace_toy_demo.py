"""Run the deterministic in-memory MetaAgent ACE toy example.

Usage: .venv/bin/python scripts/ace_toy_demo.py [directory]
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from thearc import ContextDocument, Hook, MetaAgent, Skill
from thearc.learning import (
    AceConfig,
    ChangeJournal,
    RunJournal,
    Session,
    SessionBundle,
    build_ace_flow,
    run_toy_flow,
)
from thearc.learning.models import Event, SourceReference


class DemoSelector:
    def __init__(self, session: Session):
        self.session = session

    def select(self, query=None):
        return [self.session]


class DemoMaterializer:
    def __init__(self, bundle: SessionBundle):
        self.bundle = bundle

    def materialize(self, session, config):
        return self.bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the in-memory MetaAgent ACE toy flow")
    parser.add_argument("root", nargs="?", type=Path, default=Path(".tmp-ace-toy"))
    parser.add_argument("--reset", action="store_true", help="reset the demo resources and journals first")
    parser.add_argument("--no-viz", action="store_true", help="skip the FlowTracker HTML output")
    parser.add_argument("--refresh-interval", type=float, default=1.0, help="FlowTracker refresh interval in seconds")
    parser.add_argument("--run-id", help="stable run ID; defaults to a UTC timestamp")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.reset:
        for path in (
            root / "ace-changes.jsonl",
            root / "ace-run.jsonl",
            root / "ace-checkpoint.json",
            root / "ace-flow.html",
        ):
            path.unlink(missing_ok=True)
    target = MetaAgent(
        name="demo",
        skills=[Skill(name="demo", description="Demo", instructions="Use the guard.")],
        context=[ContextDocument(filename="AGENTS.md", content="# Rules\n\nUse the guard.\n")],
        hooks=[Hook(name="guard", command="echo guard")],
    )
    session = Session(id="demo-session", source_id="demo", harness="pi", native_id="demo-native")
    event = Event(
        id="demo-event",
        source_id="demo",
        harness="pi",
        session_id=session.id,
        run_id="demo-run",
        kind="tool_result",
        status="success",
        text="guard succeeded",
        reference=SourceReference(path=root / "transcript.jsonl", generation=0, byte_offset=0, byte_length=0, line=1),
    )
    journal = ChangeJournal(root / "ace-changes.jsonl")
    flow = build_ace_flow()
    viz_path = None if args.no_viz else root / "ace-flow.html"
    run_id = args.run_id or f"demo-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}"
    result = run_toy_flow(
        selector=DemoSelector(session),
        materializer=DemoMaterializer(SessionBundle(session=session, events=[event])),
        agent=target,
        journal=journal,

        config=AceConfig(sessions_per_reflection=1, reflections_per_curation=1),
        run_id=run_id,
        viz_path=viz_path,
        refresh_interval=args.refresh_interval,
        run_journal=RunJournal(root / "ace-run.jsonl"),
        checkpoint_path=root / "ace-checkpoint.json",
        flow=flow,
    )
    print(result.model_dump_json(indent=2, exclude={"agent"}))
    print(f"run ID: {run_id}")
    print(f"journal: {journal.path}")
    print("adapted MetaAgent is available as result.agent; install it explicitly after review")
    if viz_path:
        print(f"flow visualization: {viz_path}")
    print(f"run journal: {root / 'ace-run.jsonl'}")
    print(f"checkpoint: {root / 'ace-checkpoint.json'}")


if __name__ == "__main__":
    main()
