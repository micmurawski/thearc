"""Reflect on Graphify through historical handoff, then curate a candidate."""

from pathlib import Path

from pydantic import Field

from thearc import MetaAgent, ResourceTarget
from thearc.resources import CurationResult, Curator, Reflection, ReflectionBaseModel, ReflectionError, Reflector
from thearc.resources.history import History

from .graphify_evidence import CURATION_PROMPT
from .graphify_inputs import arguments, load_arguments, source_context


class Finding(ReflectionBaseModel):
    resource: ResourceTarget = Field(description="Existing Graphify skill or related guidance resource")
    observation: str = Field(description="Specific behavior and outcome observed in the inherited conversation")
    evidence_excerpt: str = Field(description="Short supporting excerpt from a message or tool call/result")
    recommendation: str = Field(description="Concrete reusable guidance change supported by that observation")


class ReflectionSchema(ReflectionBaseModel):
    summary: str = Field(description="Graphify's contribution to the original user's task")
    findings: list[Finding] = Field(description="Supported observations; [] if none justify a change")
    limitations: list[str] = Field(description="Missing evidence and uncertainty about causality or outcomes")


REFLECTION_PROMPT = """Reflect on the conversation you inherited and Graphify's contribution
to its outcome. Inspect exploration, graph queries, error recovery, validation, and graph updates.
Do not continue the original coding task. Explain observed behavior using short excerpts and
recommend reusable guidance improvements. Separate tool-confirmed results from assistant claims."""


def reflect(
    agent: MetaAgent,
    history: History,
    *,
    model: str,
    output: str | Path,
    implementation: str = "codex",
    runner=None,
    source_agent: MetaAgent | None = None,
) -> Reflection:
    require_handoff(implementation, runner)
    reflector = Reflector(implementation=implementation, model=model, artifact_dir=output)
    # The history is inherited by the runtime. No Markdown evidence is appended to RP.
    return reflector.handoff(
        REFLECTION_PROMPT + source_context(agent, source_agent),
        history,
        agent=agent,
        agent_mode="reference",
        output_schema=ReflectionSchema,
        validate_resources=True,
    )


def require_handoff(implementation, runner):
    if implementation != "codex" or runner is not None:
        raise ReflectionError(
            "capability",
            "Historical handoff currently requires the built-in Codex runtime. "
            "Use graphify_evidence for Claude, Antigravity, or a custom prompt runner; "
            "translation alone does not provide runtime history import.",
        )


def adapt(
    agent: MetaAgent,
    history: History,
    *,
    model: str,
    output: str | Path,
    implementation: str = "codex",
    runner=None,
    source_agent: MetaAgent | None = None,
) -> CurationResult:
    require_handoff(implementation, runner)
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=False)
    agent.to_workspace(destination / "baseline")
    if source_agent is not None:
        (destination / "source-agent.json").write_text(source_agent.model_dump_json(indent=2), encoding="utf-8")
    reflection = reflect(
        agent,
        history,
        model=model,
        output=destination / "reflections",
        implementation=implementation,
        source_agent=source_agent,
    )
    curator = Curator(implementation=implementation, model=model, artifact_dir=destination / "curator-runs")
    # Custom observations inform edits. They do not automatically become resource ratings.
    # Passing Reflection also lets the built-in Codex curator inspect its inherited history.
    return curator.curate(
        CURATION_PROMPT + source_context(agent, source_agent),
        [reflection],
        agent=agent,
        output=destination / "curation",
    )


def main(argv=None):
    args = arguments(__doc__, argv)
    require_handoff(args.implementation, args.runner)
    inputs = load_arguments(args)
    agent = inputs.agent
    result = adapt(
        agent,
        inputs.histories[0],
        model=args.model,
        output=args.output,
        implementation=args.implementation,
        source_agent=inputs.source_agents[0],
    )
    print(result.summary)
    print(agent.diff(result.agent))


if __name__ == "__main__":
    main()
