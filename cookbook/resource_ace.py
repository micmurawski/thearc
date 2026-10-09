"""Adapt a MetaAgent using explicit Markdown evidence and validated assessments.

Calling adapt() invokes the configured runtime. Importing this module performs no I/O.
"""

from pathlib import Path

from thearc import MetaAgent
from thearc.resources import CurationResult, Curator, Reflector, ResourceAssessmentReport
from thearc.resources.history import History

REFLECTION_PROMPT = """Assess how the supplied MetaAgent resources contributed to the recorded behavior.
Rate only resources whose contribution is supported by cited event IDs. Explain uncertainty.
Suggest concrete improvements; do not infer historical installation from the current catalog."""


def adapt(agent: MetaAgent, history: History, *, implementation: str, model: str, output: str | Path) -> CurationResult:
    destination = Path(output).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False)
    agent.to_workspace(destination / "baseline")
    (destination / "evidence.jsonl").write_text(history.to_jsonl(), encoding="utf-8")
    reflector = Reflector(implementation=implementation, model=model, artifact_dir=destination / "reflections")
    reflection = reflector.reflect(
        REFLECTION_PROMPT + "\n\n" + history.to_markdown(),
        agent=agent,
        output_schema=ResourceAssessmentReport,
        validate_resources=True,
    )
    assessments = reflection.assessments(history)
    curator = Curator(implementation=implementation, model=model, artifact_dir=destination / "curator-runs")
    result = curator.curate(
        "Improve the configuration using the supported findings.",
        [assessments],
        agent=agent,
        output=destination / "curation",
    )
    return result
