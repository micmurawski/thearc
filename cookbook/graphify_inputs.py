"""Load captured Graphify prompt runs without invoking a model."""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from thearc import MetaAgent
from thearc.learning.reflection.cli import load_agent, runner_for
from thearc.learning.sessions.models import SourceConfig
from thearc.resources import Session, SessionStore, Trajectory

DEFAULT_BATCH = Path("logs/codex-prompts/fastapi-worktrees-20260927-live")


@dataclass(frozen=True)
class Inputs:
    agent: MetaAgent
    histories: tuple[Trajectory, ...]
    source_agents: tuple[MetaAgent, ...]


def select_trajectory(session, index):
    if index < 0:
        raise ValueError("trajectory_index must be nonnegative")
    captured = Session.from_events(session.events, metadata=session.metadata)
    parts = captured.split_into_trajectories()
    if index >= len(parts):
        raise ValueError(f"Session {session.id} has only {len(parts)} trajectories")
    return parts[index]


def choose_target(agents, target=None):
    if target is None:
        target = agents[0]
        if any(agent.model_dump() != target.model_dump() for agent in agents[1:]):
            raise ValueError("Sessions need identical recorded baselines or an explicit target agent")
    if "graphify" not in target.skills:
        raise ValueError("The target agent has no graphify skill")
    return target


def load_inputs(path: str | Path, *, target: MetaAgent | None = None) -> Inputs:
    """Load mixed native sources or saved Session artifacts from an explicit input manifest.

    Paths are relative to the manifest. Each entry associates a historical session
    with its producer configuration; the adaptation target is a separate choice.
    """
    path = Path(path).expanduser().resolve()
    entries = json.loads(path.read_text(encoding="utf-8"))["sessions"]
    if not entries:
        raise ValueError("Provide at least one session")

    def local(value):
        return (path.parent / Path(value).expanduser()).resolve()

    agents = tuple(load_agent(local(entry["agent"])) for entry in entries)
    target = choose_target(agents, target)
    histories = []
    with (
        TemporaryDirectory(prefix="graphify-inputs-") as temporary,
        SessionStore(Path(temporary) / "sessions.sqlite") as store,
    ):
        sources = {}
        for entry in entries:
            if ("artifact" in entry) == ("source" in entry):
                raise ValueError("Each session needs exactly one of artifact or source")
            if "source" in entry:
                config = dict(entry["source"])
                config["root"] = local(config["root"])
                if config.get("harness") == "agy":
                    config["harness"] = "antigravity"
                source = SourceConfig.model_validate(config)
                if source.id in sources and sources[source.id] != source:
                    raise ValueError(f"Conflicting configurations for source {source.id}")
                sources[source.id] = source
        for source in sources.values():
            store.ingest(source)
        sessions = {(hit.session.metadata.source_id, hit.session.metadata.native_id): hit.session
                    for hit in store.search(roles=["user"])} if sources else {}
        for entry in entries:
            if "artifact" in entry:
                session = Session.open(local(entry["artifact"]))
            else:
                key = (entry["source"]["id"], entry["native_session_id"])
                if key not in sessions:
                    raise ValueError(f"No indexed conversation for source/session {key}")
                session = sessions[key]
            histories.append(select_trajectory(session, entry.get("trajectory", 0)))
    return Inputs(target, tuple(histories), agents)


def source_context(target, source_agent=None):
    if source_agent is None or source_agent.model_dump() == target.model_dump():
        return "\nThe target MetaAgent is also the supplied producer configuration."
    return ("\nThe session was produced by a DIFFERENT agent. The target catalog is the configuration to improve. "
            "Use the source behavior to suggest applicable improvements, not to claim the target was installed. "
            "Resource findings must address the target; their ratings will not be propagated.\n"
            "SOURCE AGENT (reference data):\n" + source_agent.without_ranks().model_dump_json())


def curation_input(reflection, history, target, source_agent=None):
    assessments = reflection.assessments(history)  # Validate citations even for cross-agent observations.
    if source_agent is not None and source_agent.model_dump() != target.model_dump():
        return reflection  # Transfer observations, never votes for another configuration's behavior.
    return assessments


def load_runs(batch: str | Path, run_ids: list[str], *, trajectory_index: int = 0
              ) -> tuple[MetaAgent, tuple[Trajectory, ...]]:
    """Use each recorded baseline and copied transcript, never the current project config."""
    inputs = load_batch(batch, run_ids, trajectory_index=trajectory_index)
    return inputs.agent, inputs.histories


def load_batch(batch: str | Path, run_ids: list[str], *, trajectory_index: int = 0,
               target: MetaAgent | None = None) -> Inputs:
    """Convenience loader for the prompt collector's batch layout."""
    if not run_ids or len(set(run_ids)) != len(run_ids):
        raise ValueError("Choose one or more distinct run IDs")
    if trajectory_index < 0:
        raise ValueError("trajectory_index must be nonnegative")
    batch = Path(batch).expanduser().resolve()
    manifests, agents = [], []
    for run_id in run_ids:
        if Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("A run ID must be a directory name")
        run = batch / "runs" / run_id
        manifest = json.loads((run / "manifest.json").read_text())
        if manifest.get("status") != "completed" or not manifest.get("transcript"):
            raise ValueError(f"Run {run_id} has no completed captured session")
        copied = batch / "sessions" / Path(manifest["transcript"]).name
        if not copied.is_file():
            raise FileNotFoundError(copied)
        manifests.append(manifest)
        agents.append(MetaAgent.model_validate_json((run / "agent-before.json").read_text()))
    baseline = choose_target(agents, target)
    harnesses = {manifest.get("harness", "codex") for manifest in manifests}
    if len(harnesses) != 1:
        raise ValueError("A collector batch must contain one harness; use load_inputs for mixed sources")

    # Detach histories before the temporary index is removed.
    with (
        TemporaryDirectory(prefix="graphify-examples-") as temporary,
        SessionStore(Path(temporary) / "sessions.sqlite") as store,
    ):
        store.ingest(SourceConfig(id="graphify-prompts", harness=harnesses.pop(), root=batch / "sessions"))
        sessions = {hit.session.metadata.native_id: hit.session for hit in store.search(roles=["user"])}
        trajectories = []
        for manifest in manifests:
            session = sessions.get(manifest["thread_id"])
            if session is None:
                raise ValueError(f"No indexed conversation for thread {manifest['thread_id']}")
            trajectories.append(select_trajectory(session, trajectory_index))
    return Inputs(baseline, tuple(trajectories), tuple(agents))


def arguments(description: str, argv=None, *, parallel: bool = False):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--run-id", action="append", help="Directory name under BATCH/runs")
    selection.add_argument("--inputs", type=Path, help="Manifest of sessions from any supported source harness")
    parser.add_argument("--agent", type=Path, help="Explicit adaptation target: MetaAgent JSON or workspace")
    parser.add_argument("--implementation", choices=["codex", "claude", "antigravity", "agy", "custom"],
                        default="codex", help="Reflection, curation, and merge runtime; independent of session format")
    parser.add_argument("--runner", help="For custom execution: trusted package.module:function")
    parser.add_argument("--trajectory", type=int, default=0)
    parser.add_argument("--model", required=True, help="Model ID for the chosen execution runtime")
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory")
    if parallel:
        parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)
    invalid_count = (len(args.run_id) < 2 if parallel else len(args.run_id) != 1) if args.run_id else False
    if invalid_count:
        parser.error("Choose at least two --run-id values" if parallel else "Choose exactly one --run-id")
    if args.implementation == "agy":
        args.implementation = "antigravity"
    args.runner = runner_for(args.implementation, args.runner)
    return args


def load_arguments(args, *, parallel=False):
    target = load_agent(args.agent) if args.agent else None
    if args.inputs:
        inputs = load_inputs(args.inputs, target=target)
    else:
        inputs = load_batch(args.batch, args.run_id, trajectory_index=args.trajectory, target=target)
    invalid_count = len(inputs.histories) < 2 if parallel else len(inputs.histories) != 1
    if invalid_count:
        raise ValueError("Choose at least two sessions" if parallel else "Choose exactly one session")
    return inputs
