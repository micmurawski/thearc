"""The smallest saved-evidence ACE recipe: reflect, curate, return a MetaAgent.

Calling adapt() invokes the explicitly selected runtime and may consume quota.
Importing this module does nothing. No flow, tracker, installation or evaluation.
"""

from pathlib import Path

from thearc import MetaAgent
from thearc.learning import EvidenceSnapshot
from thearc.learning.curation import create_curator, run_curation
from thearc.learning.evidence.snapshot import check_destination
from thearc.learning.reflection import ReflectorConfig, create_reflector


def adapt(
    agent: MetaAgent, evidence: EvidenceSnapshot, *, backend: str, model: str,
    output: str | Path,
) -> MetaAgent:
    """Adapt one configuration from a small snapshot; preserve inputs and provenance."""
    config = ReflectorConfig(model=model)
    reflector = create_reflector(backend, config)
    curator = create_curator(backend, config)
    # Preflight before creating artifacts or invoking either runtime.
    reflector.prepare_snapshot(evidence, agent)
    output = check_destination(output, tuple(evidence.manifest["blocked_root_hashes"]))
    output.mkdir(parents=True, mode=0o700)
    agent.to_workspace(output / "baseline")
    evidence.save(output / "evidence")
    reflector.artifact_dir = output / "reflections"

    reflection = reflector.reflect_snapshot(evidence, agent)
    epoch = run_curation(agent, [reflection], curator, output=output / "curation")
    return MetaAgent.model_validate(epoch["agent"])
