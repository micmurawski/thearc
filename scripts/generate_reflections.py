"""Generate reflections for the complete experimental corpus, without running ACE curation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from thearc import MetaAgent
from thearc.learning import CodexReflector, CodexReflectorConfig, HistoryService, ReflectionError
from thearc.learning.reflections import run_reflections


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("logs/ace-fastapi-sessions-20260926/history.sqlite"))
    parser.add_argument("--source-id", action="append", help="Defaults to fastapi-prompt-batch")
    parser.add_argument("--project", type=Path, default=Path("tests/fastapi"))
    parser.add_argument("--implementation", default="codex", choices=["codex", "claude", "antigravity", "pi"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--execute", action="store_true", help="Invoke the SDK; otherwise only prepare/review inputs")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.index.is_file():
        parser.error("Existing history index required; ingest sessions before running this flow")
    if args.resume and args.output is None:
        parser.error("--resume requires --output")
    agent = MetaAgent.from_project(args.implementation, args.project)
    if "graphify" not in agent.skills:
        parser.error("The selected project has no graphify skill")
    # This experiment evaluates Graphify plus project context/hooks, not unrelated skills or MCP credentials.
    agent.skills = {"graphify": agent.skills["graphify"]}
    agent.hooks = {name: hook for name, hook in agent.hooks.items() if "graphify" in hook.model_dump_json().lower()}
    agent.context = {name: doc for name, doc in agent.context.items() if "graphify" in doc.content.lower()}
    agent.resources = {name: resource for name, resource in agent.resources.items()
                       if "graphify" in resource.content.lower()}
    agent.mcps = {}
    output = args.output or Path("logs/session-reflections") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    reflector = CodexReflector(CodexReflectorConfig(model=args.model, timeout_seconds=args.timeout))
    try:
        with HistoryService(args.index) as history:
            result = run_reflections(
                history, agent, reflector, output, source_ids=args.source_id or ["fastapi-prompt-batch"],
                batch_size=args.batch_size, execute=args.execute, resume=args.resume,
            )
    except (ValueError, ReflectionError, FileExistsError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, indent=2))
    if result["failed_batches"] or result["status"] == "no_evidence":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
