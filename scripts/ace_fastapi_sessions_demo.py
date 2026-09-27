"""Run toy ACE, or explicitly preview/generate real SDK reflections (no curation)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from thearc import MetaAgent
from thearc.learning import (
    AceConfig,
    AcePipeline,
    AceQuery,
    ChangeJournal,
    CodexReflector,
    CodexReflectorConfig,
    HistoryService,
    HistorySessionMaterializer,
    HistorySessionSelector,
    ReflectionError,
    RunJournal,
    SearchFilters,
    SourceConfig,
    build_ace_flow,
    run_toy_flow,
)
from thearc.learning.ace import chunks
from thearc.learning.codex_reflector import redact


def run_toy() -> None:
    root = Path("logs/ace-fastapi-sessions-20260926").resolve()
    agent = MetaAgent.from_project("codex", Path("tests/fastapi"))
    run_id = datetime.now(timezone.utc).strftime("metaagent-%Y%m%dT%H%M%S.%fZ")
    output = root / run_id
    output.mkdir(parents=True)
    (output / "before.json").write_text(agent.model_dump_json(indent=2), encoding="utf-8")
    index = root / "history.sqlite"
    with HistoryService(index) as history:
        history.register_source(SourceConfig(id="fastapi-prompt-batch", harness="codex", root=root))
        history.sync()
        result = run_toy_flow(
            selector=HistorySessionSelector(history),
            materializer=HistorySessionMaterializer(history),
            agent=agent,
            journal=ChangeJournal(output / "changes.jsonl"),
            config=AceConfig(sessions_per_reflection=10, reflections_per_curation=1),
            run_id=run_id,
            viz_path=output / "flow.html",
            run_journal=RunJournal(output / "run.jsonl"),
            checkpoint_path=output / "checkpoint.json",
            flow=build_ace_flow(),
        )
    print(result.model_dump_json(indent=2, exclude={"agent"}))
    (output / "after.json").write_text(result.agent.model_dump_json(indent=2), encoding="utf-8")
    print(f"MetaAgent snapshots and journal: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["dry-run", "reflect", "toy", "list"], default="dry-run")
    parser.add_argument("--index", type=Path, default=Path("logs/ace-fastapi-sessions-20260926/history.sqlite"))
    parser.add_argument("--project", type=Path, default=Path("tests/fastapi"))
    parser.add_argument("--implementation", choices=["codex", "claude", "antigravity", "pi"], default="codex")
    parser.add_argument("--skill", default="graphify")
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--model", help="Explicit Codex model; required for preview and live reflection")
    parser.add_argument("--reasoning-effort", default="medium",
                        choices=["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"])
    parser.add_argument("--resume", action="store_true", help="Reuse validated matching batches in --output")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-input-chars", type=int, default=300_000)
    parser.add_argument("--output", type=Path, help="New artifact directory outside the session corpus")
    args = parser.parse_args()
    if args.mode == "toy":
        run_toy()
        return
    if not args.index.is_file():
        parser.error("Index does not exist; ingest the source sessions first")
    if args.mode != "list" and (not args.model or not args.session_id):
        parser.error("Supply --model and explicit --session-id values (use --mode list first)")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.resume and (args.mode != "reflect" or args.output is None):
        parser.error("--resume requires --mode reflect and an explicit --output")
    with HistoryService(args.index) as history:
        if args.mode == "list":
            offset = 0
            while sessions := history.list_sessions(limit=100, offset=offset):
                for session in sessions:
                    print(json.dumps({"id": session.id, "harness": session.harness,
                                      "source": session.source_id, "events": session.event_count}))
                offset += len(sessions)
            return
        query = AceQuery(filters=SearchFilters(session_ids=args.session_id))
        selector = HistorySessionSelector(history)
        sessions = list(selector.select(query))
        if {s.id for s in sessions} != set(args.session_id):
            parser.error("Some requested session IDs were not found; no inference was started")
        agent = MetaAgent.from_project(args.implementation, args.project)
        if args.skill not in agent.skills:
            parser.error(f"Skill {args.skill!r} is not present in the imported MetaAgent")
        # Keep the selected skill and associated guidance; selection is explicit
        # and outside the generic reflector. All nested skill files remain intact.
        agent.skills = {args.skill: agent.skills[args.skill]}
        agent.hooks = {name: hook for name, hook in agent.hooks.items()
                       if args.skill.casefold() in hook.model_dump_json().casefold()}
        agent.context = {name: doc for name, doc in agent.context.items()
                         if args.skill.casefold() in doc.content.casefold()}
        agent.resources = {name: item for name, item in agent.resources.items()
                           if args.skill.casefold() in item.content.casefold()}
        agent.mcps = {}
        output = args.output or Path("logs/ace-reflections") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = output.resolve()
        # Source roots are read from the index, without syncing or registering
        # a new source. Reflection runs must never become their own evidence.
        roots = [source.root for source in history.list_sources()]
        if any(output == root or root in output.parents for root in roots):
            parser.error("Output must be outside every indexed session source root")
        output.mkdir(parents=True, exist_ok=args.resume)
        config = CodexReflectorConfig(model=args.model, timeout_seconds=args.timeout,
                                      reasoning_effort=args.reasoning_effort, max_input_chars=args.max_input_chars)
        reflector = CodexReflector(config, artifact_dir=output, resume=args.resume)
        ace_config = AceConfig(sessions_per_reflection=args.batch_size, max_text_chars_per_event=6000)
        materializer = HistorySessionMaterializer(history)
        manifest = {"mode": args.mode, "session_ids": [s.id for s in sessions],
                    "import_implementation": args.implementation, "skill": args.skill,
                    "settings": config.model_dump(), "batch_size": args.batch_size,
                    "imported_config_sha256": hashlib.sha256(
                        json.dumps(agent.model_dump(mode="json"), sort_keys=True).encode()
                    ).hexdigest()}
        manifest_path = output / "manifest.json"
        if manifest_path.exists():
            if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
                parser.error("Resume settings/session selection differ; use a new output directory")
        else:
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            (output / "imported-snapshot.json").write_text(
                json.dumps(redact(agent.model_dump(mode="json")), indent=2) + "\n", encoding="utf-8",
            )
        if args.mode == "dry-run":
            bundles = [materializer.materialize(session, ace_config) for session in sessions]
            for number, batch in enumerate(chunks(bundles, args.batch_size), start=1):
                prepared = reflector.prepare(batch, agent)
                (output / f"batch-{number:03d}.json").write_text(
                    json.dumps(prepared, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                )
            print(f"Prepared {len(sessions)} sessions in {output}; no SDK inference was performed.")
        else:
            result = AcePipeline(selector, materializer, reflector, config=ace_config).run(
                agent, query, reflection_only=True,
            )
            result_path = output / f"result-{result.run_id}.json"
            result_path.write_text(
                json.dumps(redact(result.model_dump(mode="json")), indent=2) + "\n", encoding="utf-8",
            )
            print(f"Generated {len(result.reflections)} reflections in {output}; configuration unchanged.")


if __name__ == "__main__":
    try:
        main()
    except ReflectionError as exc:
        print(f"Reflection failed [{exc.code}]: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
