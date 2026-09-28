"""Toy in-memory MetaAgent ACE implementation.

This is intentionally small and deterministic. It demonstrates the separation
between an unranked reflector, a rank-aware curator, reasoned resource changes,
and an append-only journal. It is not a production updater or an LLM client.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from thearc.learning.ace.pipeline import (
    AceConfig,
    AcePipeline,
    AceQuery,
    Curation,
    Reflection,
    SessionBundle,
    UpdateResult,
)
from thearc.learning.runtime.journal import AdaptationRun, CheckpointStore, RunJournal
from thearc.models import MarkdownDocument, MetaAgent, Operation, Ranks, ResourceTarget

LOGGER = logging.getLogger("thearc.ace.toy")


class ChangeReason(BaseModel):
    """A non-empty explanation required for every persisted resource change."""

    summary: str = Field(min_length=1)
    evidence_event_ids: list[str] = Field(default_factory=list)
    reflection_ids: list[str] = Field(default_factory=list)
    actor: str = "ace-toy"

    @field_validator("summary")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("change reason must contain non-whitespace text")
        return value.strip()


class RankProposal(BaseModel):
    target: ResourceTarget
    delta: Ranks = Field(default_factory=Ranks)
    reason: ChangeReason


class JournalEntry(BaseModel):
    run_id: str
    change_id: str
    target: ResourceTarget
    operation: Operation
    before_sha256: str
    after_sha256: str
    reason: ChangeReason
    created_at: str


class ChangeJournal:
    """Append-only JSONL journal stored beside the managed resources."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: JournalEntry) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(entry.model_dump_json() + "\n")
        LOGGER.info("journaled %s change for %s:%s", entry.change_id, entry.target.kind, entry.target.name)

    def record(self, run_id: str, curation: Curation, before: Any, after: Any) -> None:
        """Record an already accepted change; validation belongs to the pipeline."""
        def digest(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

        self.append(JournalEntry(
            run_id=run_id, change_id=curation.id, target=curation.target, operation=curation.operation,
            before_sha256=digest(before), after_sha256=digest(after),
            reason=ChangeReason(
                summary=curation.rationale, reflection_ids=curation.reflection_ids,
                evidence_event_ids=curation.evidence_event_ids, actor="ace-curator",
            ),
            created_at=datetime.now(timezone.utc).isoformat(),
        ))

    def entries(self) -> list[JournalEntry]:
        if not self.path.exists():
            return []
        return [
            JournalEntry.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line
        ]


class ToyReflector:
    """Generate rank proposals from events while explicitly hiding prior ranks."""

    def reflect(self, batch: Sequence[SessionBundle], target: MetaAgent) -> Reflection:
        # The reflector receives a rank-free projection. This is the boundary
        # that prevents historical scores from biasing the observation.
        rank_free = target.without_ranks()

        failures = [event for bundle in batch for event in bundle.events if event.status == "failure"]
        successes = [event for bundle in batch for event in bundle.events if event.status == "success"]
        LOGGER.info("reflecting %d sessions: %d successes, %d failures", len(batch), len(successes), len(failures))
        evidence = [event.id for event in failures[:3] + successes[:3]]
        proposals: list[RankProposal] = []
        if rank_free.skills:
            name = next(iter(rank_free.skills.values())).name
            proposals.append(
                RankProposal(
                    target=ResourceTarget(kind="skill", name=name),
                    delta=Ranks(harmful=len(failures), helpful=len(successes)),
                    reason=ChangeReason(
                        summary="Toy reflector counted execution outcomes in the session batch",
                        evidence_event_ids=evidence,
                    ),
                )
            )
        if skill_files := list(rank_free.iter_skill_files(markdown_only=True)):
            name, _ = skill_files[0]
            proposals.append(
                RankProposal(
                    target=ResourceTarget(kind="skill_file", name=name),
                    delta=Ranks(harmful=len(failures), helpful=len(successes)),
                    reason=ChangeReason(
                        summary="Toy reflector scored a skill reference from execution outcomes",
                        evidence_event_ids=evidence,
                    ),
                )
            )
        if rank_free.context:
            document = next(iter(rank_free.context.values()))
            parsed = MarkdownDocument.parse(document.content)
            if parsed.sections:
                proposals.append(
                    RankProposal(
                        target=ResourceTarget(kind="context", name=document.filename, section=parsed.sections[0].title),
                        delta=Ranks(harmful=len(failures), helpful=len(successes)),
                        reason=ChangeReason(
                            summary="Toy reflector scored the first context section", evidence_event_ids=evidence
                        ),
                    )
                )
        if rank_free.hooks:
            name = next(iter(rank_free.hooks.values())).name
            proposals.append(
                RankProposal(
                    target=ResourceTarget(kind="hook", name=name),
                    delta=Ranks(harmful=len(failures), helpful=len(successes)),
                    reason=ChangeReason(
                        summary="Toy reflector scored hook-related execution outcomes", evidence_event_ids=evidence
                    ),
                )
            )
        return Reflection(
            id="reflection-" + hashlib.sha256("|".join(b.session.id for b in batch).encode()).hexdigest()[:16],
            session_ids=[bundle.session.id for bundle in batch],
            observations=[f"failures={len(failures)}", f"successes={len(successes)}"],
            evidence_event_ids=evidence,
            rank_proposals=[proposal.model_dump() for proposal in proposals],
        )


class ToyCurator:
    """Accept proposals using current ranks, which the reflector never sees."""

    def __init__(self):
        self.seen_ranks: list[dict[str, Any]] = []

    def curate(self, batch: Sequence[Reflection], agent: MetaAgent) -> list[Curation]:
        curations: list[Curation] = []
        totals: dict[ResourceTarget, Ranks] = {}
        observed: dict[str, dict[str, int]] = {}
        for reflection in batch:
            for raw in reflection.rank_proposals:
                proposal = RankProposal.model_validate(raw)
                address = proposal.target
                current = totals.get(address) or agent.rank_for(address)
                observed[f"{address.kind}:{address.name}:{address.section}"] = current.model_dump()
                # Toy policy: keep helpful evidence, but suppress new harmful
                # votes once historical harmful votes substantially dominate.
                if proposal.delta.harmful and current.harmful > current.helpful + 2:
                    continue
                updated = Ranks(**{
                    field: getattr(current, field) + getattr(proposal.delta, field)
                    for field in ("harmful", "neutral", "helpful")
                })
                totals[address] = updated
                value = {"ranks": updated.model_dump()}
                if address.section is None and address.kind in {"skill_file", "context", "rule", "workflow", "command"}:
                    content = agent.read_target(address)
                    document = MarkdownDocument.parse(content if isinstance(content, str) else content["content"])
                    document.frontmatter["ranks"] = updated.model_dump()
                    text = document.to_markdown()
                    value = text if address.kind == "skill_file" else {"content": text}
                curations.append(Curation(
                    id=f"curation-{reflection.id}-{len(curations)}",
                    target=address,
                    operation=Operation.EDIT,
                    value=value,
                    reflection_ids=[reflection.id],
                    evidence_event_ids=proposal.reason.evidence_event_ids,
                    rationale=f"Curator accepted proposal with current ranks {current.model_dump()}",
                ))
        self.seen_ranks.append(observed)
        LOGGER.info("curated %d reflections: accepted %d curations", len(batch), len(curations))
        return curations


def run_toy_flow(
    *,
    selector: Any,
    materializer: Any,
    agent: MetaAgent,
    journal: ChangeJournal,
    config: AceConfig | None = None,
    query: AceQuery | None = None,
    run_id: str = "ace-toy-flow",
    viz_path: str | Path | None = None,
    refresh_interval: float = 1.0,
    run_journal: RunJournal | None = None,
    checkpoint_path: str | Path | None = None,
    flow: Any | None = None,
) -> UpdateResult:
    """Execute the toy implementation through the Flow graph.

    When ``viz_path`` is supplied, the existing ``FlowTracker`` writes a live
    HTML status page before, during, and after execution.
    """
    lifecycle = AdaptationRun(
        run_id,
        flow_name="ace-toy-flow",
        flow_version="3",
        journal=run_journal,
        checkpoint=CheckpointStore(checkpoint_path) if checkpoint_path else None,
    )
    pipeline = AcePipeline(
        selector, materializer, ToyReflector(), ToyCurator(), config,
        on_change=partial(journal.record, run_id),
    )
    with lifecycle:
        result = pipeline.run(
            agent, query, run_id=run_id, viz_path=viz_path,
            refresh_interval=refresh_interval, flow=flow,
        )
        lifecycle.complete(
            result.model_dump(mode="json", exclude={"run_id"}), node="curation",
            completed_nodes=["query", "materialize", "reflection", "curation"],
        )
        return result.update
