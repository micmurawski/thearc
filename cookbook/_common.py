"""Small shared helpers for the recipes, not an additional library API."""

import argparse
from pathlib import Path

from thearc import MetaAgent
from thearc.learning import (
    AntigravityReflector,
    AntigravityReflectorConfig,
    ClaudeReflector,
    ClaudeReflectorConfig,
    CodexReflector,
    CodexReflectorConfig,
    Event,
    EvidenceSnapshot,
    Session,
    SessionBundle,
)
from thearc.learning.evidence.snapshot import check_destination, write_json_exclusive
from thearc.learning.privacy import hide_historical_ranks, redact


def project_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path("tests/fastapi"))
    parser.add_argument("--implementation", choices=["codex", "claude", "antigravity", "pi"], default="codex")
    parser.add_argument("--skill", default="graphify", help="Evaluate this skill and matching context/hooks/resources")


def load_config(args) -> MetaAgent:
    agent = MetaAgent.from_project(args.implementation, args.project)
    if args.skill not in agent.skills:
        raise ValueError(f"Skill {args.skill!r} was not found in {args.project}")
    agent.skills = {args.skill: agent.skills[args.skill]}
    agent.hooks = {name: hook for name, hook in agent.hooks.items()
                   if args.skill.lower() in hook.model_dump_json().lower()}
    agent.context = {name: doc for name, doc in agent.context.items() if args.skill.lower() in doc.content.lower()}
    agent.resources = {name: resource for name, resource in agent.resources.items()
                       if args.skill.lower() in resource.content.lower()}
    agent.mcps = {}
    return agent


def reflection_arguments(parser: argparse.ArgumentParser, *, builtin: bool = True) -> None:
    parser.add_argument("--model", required=True, help="An explicit model ID supported by the selected runtime")
    if builtin:
        parser.add_argument("--backend", choices=["codex", "claude", "antigravity"], default="codex")
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory; never overwritten")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--execute", action="store_true", help="Opt in to model calls; otherwise preview only")


def bundles(evidence: EvidenceSnapshot) -> list[SessionBundle]:
    """Bridge frozen normalized records to the existing INLINE reflector API.

    This does not manufacture native rollouts or preserve an agent's hidden state.
    The reflector applies its own input limits; the snapshot remains complete.
    """
    events = {session["id"]: [] for session in evidence.sessions}
    omitted = {session_id: [] for session_id in events}
    for event in evidence.iter_events():
        events[event["session_id"]].append(Event.model_validate(event))
    for exclusion in evidence.manifest["omitted_events"]:
        omitted[exclusion["session_id"]].append(exclusion["id"])
    return [SessionBundle(session=Session.model_validate(session), events=events[session["id"]],
                          omitted_event_ids=omitted[session["id"]]) for session in evidence.sessions]


def reflect(evidence: EvidenceSnapshot, agent: MetaAgent, args, *, handoff_sha256: str | None = None) -> dict:
    """Save exact inputs, then optionally generate real findings. No toy ratings."""
    backend = getattr(args, "backend", "codex")
    reflector_type, config_type = {
        "codex": (CodexReflector, CodexReflectorConfig),
        "claude": (ClaudeReflector, ClaudeReflectorConfig),
        "antigravity": (AntigravityReflector, AntigravityReflectorConfig),
    }[backend]
    config = config_type(model=args.model, timeout_seconds=args.timeout)
    policy = evidence.manifest["policy"]
    if not policy["redact"] or not policy["hide_ranks"]:
        raise ValueError("Use a redacted, rank-free evidence snapshot")
    output = check_destination(args.output, tuple(evidence.manifest["blocked_root_hashes"]))
    evidence.save(output / "evidence")
    write_json_exclusive(output / "configuration.json", redact(hide_historical_ranks(
        agent.without_ranks().model_dump(mode="json")
    )))
    write_json_exclusive(output / "source.json", {
        "snapshot_sha256": evidence.sha256, "handoff_sha256": handoff_sha256,
        "mode": "inline", "execute": args.execute, "backend": backend,
        "note": "Fresh reflection of captured evidence; no handoff execution or native continuation.",
    })
    results = []
    for number, bundle in enumerate(bundles(evidence), 1):
        folder = output / f"session-{number:04d}"
        folder.mkdir()
        reflector = reflector_type(config, artifact_dir=folder)
        prepared = reflector.prepare([bundle], agent)
        write_json_exclusive(folder / "preview.json", prepared)
        result = {"session_id": bundle.session.id, "preview": str(folder / "preview.json")}
        if args.execute:
            reflection = reflector.reflect([bundle], agent)
            result["reflection"] = str(folder / reflection.id / "reflection.json")
        results.append(result)
    summary = {"status": "completed" if args.execute else "preview_only", "sessions": results}
    write_json_exclusive(output / "summary.json", summary)
    return summary
