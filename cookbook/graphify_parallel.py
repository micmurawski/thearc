"""Adapt Graphify in parallel from multiple prompt runs, then resolve and merge candidates."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import Field

from thearc import MetaAgent
from thearc.resources import (
    CurationResult,
    Curator,
    Merger,
    MergeResult,
    Reflector,
    ResourceAssessment,
    ResourceAssessmentReport,
)
from thearc.resources.history import History

from .graphify_evidence import CURATION_PROMPT, REFLECTION_PROMPT
from .graphify_inputs import arguments, curation_input, load_arguments, source_context


class ReflectionSchema(ResourceAssessmentReport):
    summary: str = Field(description="What this single Graphify run supports changing in the shared baseline")
    findings: list[ResourceAssessment] = Field(description=(
        "Resource assessments citing only this run's provided event IDs. Each reason must include "
        "the observed effect, a proposed guidance improvement, and when that improvement applies."
    ))
    limitations: list[str] = Field(description="Single-run uncertainty and cases that other runs should check")


def adapt(agent: MetaAgent, histories: Sequence[History], *, model: str, output: str | Path,
          max_workers: int = 2, implementation: str = "codex", runner=None,
          source_agents: Sequence[MetaAgent] | None = None) -> MergeResult:
    histories = tuple(histories)
    if len(histories) < 2 or max_workers < 1:
        raise ValueError("Supply at least two histories and a positive max_workers")
    source_agents = tuple(source_agents) if source_agents is not None else (None,) * len(histories)
    if len(source_agents) != len(histories):
        raise ValueError("Supply one source agent per history")
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=False)
    baseline = agent.model_copy(deep=True)
    baseline.to_workspace(destination / "baseline")

    def candidate(item: tuple[int, History]) -> CurationResult:
        index, history = item
        branch = destination / f"candidate-{index:03d}"
        branch.mkdir()
        branch_agent = baseline.model_copy(deep=True)
        source_agent = source_agents[index]
        if source_agent is not None:
            (branch / "source-agent.json").write_text(source_agent.model_dump_json(indent=2), encoding="utf-8")
        prompt = (REFLECTION_PROMPT + source_context(branch_agent, source_agent)
                  + "\n\n## Recorded evidence\n\n" + history.to_markdown())
        (branch / "reflection-prompt.md").write_text(prompt, encoding="utf-8")
        reflector = Reflector(implementation=implementation, model=model, runner=runner,
                              artifact_dir=branch / "reflections")
        reflection = reflector.reflect(
            prompt, agent=branch_agent, output_schema=ReflectionSchema, validate_resources=True,
        )
        assessments = curation_input(reflection, history, branch_agent, source_agent)
        curator = Curator(implementation=implementation, model=model, runner=runner,
                          artifact_dir=branch / "curator-runs")
        return curator.curate(CURATION_PROMPT + source_context(branch_agent, source_agent), [assessments],
                              agent=branch_agent, output=branch / "curation")

    # Each worker owns its runtime objects, baseline copy, and output paths.
    # A failed branch raises; merging starts only after every candidate succeeds.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        candidates = list(executor.map(candidate, enumerate(histories)))
    resolver = Curator(implementation=implementation, model=model, runner=runner,
                      artifact_dir=destination / "resolver-runs")
    return Merger(resolver=resolver).merge(
        base=baseline, candidates=candidates,
        prompt=("Combine the supported Graphify guidance improvements. Use each run's evidence and "
                "limitations to resolve competing edits. Preserve uncertainty; leave conflicts unresolved "
                "when the evidence does not justify a choice."),
        review=True, output=destination / "merge",
    )


def main(argv=None):
    args = arguments(__doc__, argv, parallel=True)
    inputs = load_arguments(args, parallel=True)
    agent = inputs.agent
    result = adapt(agent, inputs.histories, model=args.model, output=args.output, max_workers=args.workers,
                   implementation=args.implementation, runner=args.runner, source_agents=inputs.source_agents)
    if result.unresolved:
        print("Merge requires review:")
        for issue in result.unresolved:
            print(issue.model_dump())
    else:
        print(agent.diff(result.agent))


if __name__ == "__main__":
    main()
