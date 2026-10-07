"""Collected sessions -> trajectory -> Codex history import -> MCP reflections -> ranks.

Run from the repository root:
    python -m cookbook.codex_example --source logs/claude-traning --model YOUR_MODEL_ID \
        --output artifacts/codex-example
Add --prepare-only to save what Codex would receive without running a model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import Field

from thearc.learning.evidence.snapshot import write_json_exclusive
from thearc.learning.reflection.runtime import ReflectionError
from thearc.resources import ReferencedReflection, Reflection, ReflectionTools, Reflector, Sessions
from thearc.resources.sessions import DEFAULT_INDEX

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "logs/claude-traning"


class ReflectionSchema(ReferencedReflection):
    """One finding about the recorded agent, citing its files by line; the MCP tool accepts a list."""

    limitations: list[str] = Field(description="Uncertainty about resource use, causality, or observed outcomes")


PROMPT = """Evaluate the agent's behavior in the inherited conversation.
The original task is historical evidence. Do not continue it or modify its code.
The import contains normalized conversation events. Harness metadata/attachments
are retained in the source artifact but are not replayed. Account for this limitation.
Look for supported lessons about navigation, tool use, recovery, and verification.
The agent's configuration captured before this task ran is available as files
(list_files, read_file); their presence alone does not prove that a resource was
loaded or caused an outcome. Cite the lines a finding is about in resource_refs as
path#Lstart-Lend, within one section or covering a whole section. Leave resource_refs
empty when attribution is uncertain. Record uncertainty in limitations.
Submit the complete list through submit_reflections, fixing any returned errors.
An empty list is valid if there are no supported findings. After acceptance, finish.
"""

def load(source, session=None, *, trajectory=0, index=DEFAULT_INDEX):
    """Index SOURCE (any harness directory or collector batch); return one trajectory and its agent."""
    with Sessions.open(index) as sessions:
        sessions.add(source)
        selected = sessions.get(session)
        agent = sessions.agent(selected)
    if agent is None:
        raise ValueError("Reflection tools need the recorded agent; use a collector batch with agent-before.json")
    return selected.trajectories[trajectory], agent


def generate_reflections(history, agent, *, model: str, output: Path, timeout: float = 300) -> list[Reflection]:
    """Hand the trajectory to Codex, collect cited findings through MCP, and count them into ranks."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    tools = ReflectionTools(agent=agent, evidence=history, reflection_schema=ReflectionSchema,
                            output=output / "submission.json")
    reflector = Reflector(implementation="codex", model=model, tool_transport="mcp",
                          timeout_seconds=timeout, artifact_dir=output / "reflection-runs")
    try:
        # Reflector owns translation, history import, MCP startup, and validation.
        reflections = reflector.handoff(PROMPT, history, agent=agent, tools=tools)
        write_json_exclusive(output / "reflections.json", [r.output.model_dump(mode="json") for r in reflections])
    except Exception as exc:
        write_json_exclusive(output / "failure.json", {
            "type": type(exc).__name__, "code": getattr(exc, "code", None),
            "message": str(exc) if isinstance(exc, ReflectionError) else "See the raised exception",
            "artifacts": "reflection-runs/",
        })
        raise
    # Each cited line range was credited to its section; count those votes into ranks.
    ranked, ledger = agent.propagate_ranks(tools.assessments, epoch_id=output.name)
    write_json_exclusive(output / "ranked-agent.json", ranked.model_dump(mode="json"))
    write_json_exclusive(output / "ledger.json", ledger.model_dump(mode="json"))
    (output / "ranked-agent.md").write_text(ranked.to_labeled_markdown(include_ranks=True), encoding="utf-8")
    return reflections


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="Transcript directory or collector batch (runs/ + sessions/)")
    parser.add_argument("--session", help="Session, native or run ID, or a unique prefix; default: most recent")
    parser.add_argument("--trajectory", type=int, default=0)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--model", help="Explicit Codex model ID; required unless --prepare-only")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path, required=True, help="New directory for reflection artifacts")
    parser.add_argument("--prepare-only", action="store_true", help="Save the Codex import without inference")
    args = parser.parse_args(argv)
    if not args.prepare_only and not args.model:
        parser.error("--model is required unless --prepare-only")
    try:
        history, agent = load(args.source, args.session, trajectory=args.trajectory, index=args.index)
        export = history.to_codex()
        print(f"Selected trajectory {args.trajectory} of {history.session.metadata.harness} session "
              f"{history.session.metadata.native_id}: {len(export.event_ids)} of "
              f"{len(history.selected_events)} events become Codex items.", flush=True)
        if args.prepare_only:
            args.output.mkdir(parents=True, exist_ok=False)
            export.save(args.output / "trajectory.codex.jsonl")
            (args.output / "trajectory.md").write_text(history.to_markdown(), encoding="utf-8")
            write_json_exclusive(args.output / "conversion.json", {"transformations": list(export.transformations)})
            agent.files().write(args.output / "agent-files")
            (args.output / "agent.md").write_text(agent.to_labeled_markdown(include_ranks=True), encoding="utf-8")
        else:
            reflections = generate_reflections(history, agent, model=args.model, output=args.output,
                                               timeout=args.timeout)
            print(f"Accepted {len(reflections)} reflections through MCP; ranks in ranked-agent.md.")
        print(f"Artifacts: {args.output.resolve()}")
    except (ValueError, LookupError, OSError, KeyError, IndexError, ReflectionError) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
