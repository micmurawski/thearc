"""Deterministic, revision-aware feedback accounting; no inference or file writes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from thearc.learning.ace.pipeline import Reflection, ReflectionEvidence, ReflectionRating
from thearc.learning.evidence.snapshot import content_hash
from thearc.models.agent import MetaAgent, ResourceTarget
from thearc.models.markdown import MarkdownDocument
from thearc.models.ranks import Ranks


class RankBaseline(BaseModel):
    """Imported counts whose individual supporting assessments are unknown."""

    model_config = ConfigDict(extra="forbid")
    target: ResourceTarget
    revision: str
    ranks: Ranks


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reflection_id: str
    item_index: int
    epoch_id: str
    target: ResourceTarget
    revision: str
    rating: ReflectionRating
    reason: str
    evidence: list[ReflectionEvidence]
    limitations: list[str]
    configuration_provenance: str
    counted: bool
    overlaps: list[str] = Field(default_factory=list)


class AssessmentLedger(BaseModel):
    """Serializable sidecar, separate from installed agent instructions.

    Retains suppressed overlaps as well as counted votes. Current counts follow
    content hashes; lifetime counts include older versions at the same address.
    Address renames/splits require future explicit lineage mappings.
    """

    model_config = ConfigDict(extra="forbid")
    policy: Literal["disjoint-evidence-v1"] = "disjoint-evidence-v1"
    reflection_hashes: dict[str, str] = Field(default_factory=dict)
    baselines: list[RankBaseline] = Field(default_factory=list)
    assessments: list[Assessment] = Field(default_factory=list)

    def ranks(self, target: ResourceTarget, *, revision: str | None = None) -> Ranks:
        """Omit revision for lifetime counts; no weighting or inferred votes."""
        totals = Ranks()
        for baseline in self.baselines:
            if baseline.target == target and (revision is None or baseline.revision == revision):
                totals = MetaAgent._add_ranks(totals, baseline.ranks)
        for item in self.assessments:
            if item.counted and item.target == target and (revision is None or item.revision == revision):
                setattr(totals, item.rating.value, getattr(totals, item.rating.value) + 1)
        return totals


def _pairs(evidence: Sequence[ReflectionEvidence]) -> set[tuple[str, str]]:
    return {(item.session_id, item.event_id) for item in evidence}


def _content(agent: MetaAgent, target: ResourceTarget):
    value = agent.read_target(target)
    if target.kind == "skill" and target.section is None:
        value.pop("files", None)  # Bundled files have their own assessment addresses.
    return value


def _inline_ranks(target: ResourceTarget) -> bool:
    return target.kind != "mcp" and not (target.kind == "skill_file" and not target.name.endswith(".md"))


def propagate_ranks(
    agent: MetaAgent,
    reflections: Sequence[Reflection],
    *,
    epoch_id: str,
    ledger: AssessmentLedger | None = None,
    allow_unverified: bool = False,
) -> tuple[MetaAgent, AssessmentLedger]:
    """Return detached ranked configuration/history, or fail without mutation.

    Production reflection manifests must match the rank-free configuration.
    Legacy/imported reflections without manifests require explicit opt-in and
    retain unknown provenance. This checks citation structure, not raw event
    existence: callers must supply already validated reflection artifacts.

    Overlapping evidence on the same target/content is retained but not counted
    again. The first observation wins; contradictions remain visible to a curator.
    MCP and non-Markdown skill-file counts stay in the sidecar: inserting rank
    metadata into executable files or MCP payloads would change their behavior.
    """
    if not epoch_id.strip():
        raise ValueError("epoch_id must not be blank")
    candidate = agent.model_copy(deep=True)
    history = ledger.model_copy(deep=True) if ledger is not None else AssessmentLedger()
    rank_free = agent.without_ranks()
    config_revision = content_hash(rank_free.model_dump(mode="json"))
    accepted_revisions = {config_revision}
    if agent.version is None:
        # Reflections written before optional MetaAgent version metadata existed.
        legacy = rank_free.model_dump(mode="json")
        legacy.pop("version", None)
        accepted_revisions.add(content_hash(legacy))
    known = {entry.target for entry in history.baselines}
    known.update(entry.target for entry in history.assessments)
    for reflection in reflections:
        digest = content_hash(reflection.model_dump(mode="json"))
        previous = history.reflection_hashes.get(reflection.id)
        if previous is not None:
            if previous != digest:
                raise ValueError(f"Reflection identity reused with different content: {reflection.id}")
            continue
        manifest = reflection.raw.get("manifest", {})
        declared = manifest.get("config_sha256")
        if declared is not None and declared not in accepted_revisions:
            raise ValueError(f"Reflection analyzed a different configuration: {reflection.id}")
        if declared is None and not allow_unverified:
            raise ValueError(f"Reflection has no configuration revision: {reflection.id}")
        provenance = manifest.get("configuration_provenance") if declared is not None else None
        for index, item in enumerate(reflection.items):
            if not item.reason.strip() or not item.evidence:
                raise ValueError("Assessments require a reason and supporting evidence")
            if any(not ref.event_id.strip() or ref.session_id not in reflection.session_ids for ref in item.evidence):
                raise ValueError("Assessment evidence is outside the reflection sessions")
            target = item.target
            if not _inline_ranks(target) and target.section is not None:
                raise ValueError("Non-Markdown files cannot have section assessments")
            value = _content(rank_free, target)
            if target.section is not None:
                # MetaAgent resolves headings case-insensitively; canonicalize
                # their spelling so alternate casing cannot inflate ranks.
                target = target.model_copy(update={"section": value["title"]})
            revision = content_hash(value)
            if target not in known:
                initial = agent.rank_for(target) if _inline_ranks(target) else Ranks()
                history.baselines.append(RankBaseline(target=target, revision=revision, ranks=initial))
                known.add(target)
            pairs = _pairs(item.evidence)
            overlaps = [entry.id for entry in history.assessments
                        if entry.target == target and entry.revision == revision and pairs & _pairs(entry.evidence)]
            history.assessments.append(Assessment(
                id=content_hash({"reflection": reflection.id, "index": index}),
                reflection_id=reflection.id, item_index=index, epoch_id=epoch_id,
                target=target, revision=revision, rating=item.rating, reason=item.reason,
                evidence=item.evidence, limitations=item.limitations,
                configuration_provenance=provenance or "unknown; historical configuration loading is unverified",
                counted=not overlaps, overlaps=overlaps,
            ))
        history.reflection_hashes[reflection.id] = digest

    # Recompute rather than incrementing the caller's counters. Retries are
    # idempotent; old-content assessments remain in history after edits/removal.
    for target in known:
        if not _inline_ranks(target):
            continue
        try:
            revision = content_hash(_content(rank_free, target))
        except KeyError:
            continue
        ranks = history.ranks(target, revision=revision)
        # Replace counters only, preserving other sections' freshly computed ranks.
        value = {"ranks": ranks.model_dump()}
        if target.section is None and target.kind in {"skill_file", "context", "rule", "workflow", "command"}:
            current = candidate.read_target(target)
            document = MarkdownDocument.parse(current if isinstance(current, str) else current["content"])
            document.frontmatter["ranks"] = ranks.model_dump()
            text = document.to_markdown()
            value = text if target.kind == "skill_file" else {"content": text}
        candidate.apply_change(target, "EDIT", value)
    return candidate, history.model_copy(deep=True)
