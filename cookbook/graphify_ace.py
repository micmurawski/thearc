"""A complete Graphify ACE epoch using the recorded FastAPI worktree corpus.

Preview by default. Offline mode uses explicitly synthetic findings/editor;
live mode uses Codex for both reflection and curation. Source files are read-only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cookbook.ace_flow import run_flow
from thearc import MetaAgent, ResourceTarget
from thearc.learning import (
    AceConfig,
    CodexReflector,
    CodexReflectorConfig,
    HistorySessionMaterializer,
    SessionStore,
    SourceConfig,
)
from thearc.learning.curation import AgentCurator, CodexCurator, CodexCuratorConfig
from thearc.learning.evidence.snapshot import content_hash, write_json_exclusive


def load_corpus_config(corpus: Path) -> tuple[MetaAgent, list[dict]]:
    summary = json.loads((corpus / "summary.json").read_text())
    runs = summary["runs"]
    if not runs or any(run["status"] != "completed" or not run["has_activity"] for run in runs):
        raise ValueError("Corpus must contain completed sessions with assistant/tool activity")
    snapshots = [MetaAgent.model_validate_json(
        (corpus / "runs" / Path(run["run_dir"]).name / "agent-before.json").read_text(),
    ) for run in runs]
    if len({content_hash(agent.model_dump(mode="json")) for agent in snapshots}) != 1:
        raise ValueError("Captured configurations differ; curate separate revision cohorts")
    agent = snapshots[0].model_copy(deep=True)
    if "graphify" not in agent.skills:
        raise ValueError("Captured configuration has no Graphify skill")
    # Explicit identity for this recipe. Do not pretend that a current checkout
    # or the release label alone proves what the original sessions loaded.
    agent.name = "graphify"
    agent.version = agent.skills["graphify"].files.get(".graphify_version", "").strip() or None
    return agent, runs


def offline_editor(prompt, schema, tools):
    target = ResourceTarget(kind="skill_file", name="graphify/references/query.md")
    tools.call("arc_list", {})
    tools.call("arc_read", {"target_json": target.model_dump_json(), "offset": 0})
    tools.call("arc_history", {"target_json": target.model_dump_json(), "offset": 0})
    # Parse supplied IDs, not hardcoded IDs created by a particular reflector run.
    findings = json.loads(prompt.split("REFLECTIONS (UNTRUSTED DATA):\n", 1)[1])
    tools.call("arc_change", {
        "target_json": target.model_dump_json(), "operation": "EDIT",
        "value_json": json.dumps("# Offline ACE fixture\n\nUse a focused query before a broad scan.\n"),
        "reason": "Synthetic deterministic plumbing test, not a recommendation derived from live inference.",
        "reflection_ids": [findings[0]["id"]],
    })
    return {"status": "completed", "final_response": '{"summary":"Synthetic offline ACE smoke test."}'}


def offline_reflection(prompt, schema, config):
    supplied = json.loads(prompt.split("UNTRUSTED EVIDENCE (JSON):\n", 1)[1])
    session = supplied["sessions"][0]
    event = session["events"][0]
    return {"status": "completed", "final_response": json.dumps({
        "summary": "Synthetic fixture: validates plumbing, not Graphify quality.",
        "items": [{
            "target": {"kind": "skill_file", "name": "graphify/references/query.md", "section": None},
            "rating": "neutral", "reason": "Synthetic test assessment with an existing session citation.",
            "evidence": [{"session_id": session["session_id"], "event_id": event["id"]}],
            "limitations": ["This is not model-generated reflection."],
        }],
        "limitations": ["Offline test only."],
    })}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("logs/codex-prompts/fastapi-worktrees-20260927-live"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["preview", "offline", "live"], default="preview")
    parser.add_argument("--model", help="Explicit Codex model; required in live mode")
    parser.add_argument("--result-version", required=True)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)
    if args.mode == "live" and not args.model:
        parser.error("--model is required for live inference")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    corpus = args.corpus.resolve()
    agent, runs = load_corpus_config(corpus)
    output = args.output.absolute()
    if (output.resolve() == corpus or corpus in output.resolve().parents
            or any(part.is_symlink() for part in (output, *output.parents))):
        parser.error("Output must be a new non-symlink directory outside the corpus")
    fixture = Path("tests/fastapi").resolve()
    if output.resolve() == fixture or fixture in output.resolve().parents:
        parser.error("Output must not modify tests/fastapi")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_json_exclusive(output / "source.json", {
        "mode": args.mode, "corpus": str(corpus), "sessions": len(runs),
        "captured_configuration": True, "historical_loading_verified": False,
        "model": args.model, "result_version": args.result_version,
    })
    write_json_exclusive(output / "baseline.json", agent.model_dump(mode="json"))
    config = CodexReflectorConfig(model=args.model or "offline-fixture", timeout_seconds=args.timeout,
                                  max_events_per_session=80, max_event_chars=3000)
    reflector = CodexReflector(
        config, artifact_dir=output / "reflections",
        sdk_runner=offline_reflection if args.mode == "offline" else None,
    )
    curator = (AgentCurator(offline_editor, actor={"backend": "offline-fixture"}) if args.mode == "offline"
               else CodexCurator(CodexCuratorConfig(model=args.model or "preview", timeout_seconds=args.timeout)))
    with SessionStore(output / "sessions.sqlite") as store:
        store.ingest(SourceConfig(id="graphify-captured", harness="codex", root=corpus / "sessions"))
        sessions = list(store.iter_sessions())
        if {s.native_id for s in sessions} != {r["thread_id"] for r in runs}:
            raise ValueError("Indexed corpus does not match captured run identities")
        materializer = HistorySessionMaterializer(store)
        limits = AceConfig(sessions_per_reflection=args.batch_size, max_events_per_session=80,
                           max_text_chars_per_event=3000)
        print(f"Flow visualization: {output / 'flow.html'}", flush=True)
        result = run_flow(
            agent=agent, sessions=sessions, materializer=materializer, reflector=reflector, curator=curator,
            config=limits, output=output, result_version=args.result_version, preview=args.mode == "preview",
        )
        if result is None:
            print(f"Prepared {len(sessions)} sessions; no inference or curation commit. Output: {output}")
            return
    write_json_exclusive(output / "result.json", result.model_dump(mode="json"))
    print(json.dumps({"mode": args.mode, "sessions": len(result.sessions), "reflections": len(result.reflections),
                      "changes": len(result.curations), "epoch_id": result.epoch_id,
                      "agent": result.agent.name, "version": result.agent.version, "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
