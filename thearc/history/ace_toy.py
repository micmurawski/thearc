"""Toy file-backed ACE implementation.

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
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from thearc.models import ContextDocument, Hook, Ranks, Skill
from thearc.models.schemas import SkillDefinition

from .ace import (
    AceConfig,
    AceContext,
    AceQuery,
    Curation,
    Playbook,
    Reflection,
    SessionBundle,
    UpdateResult,
    build_ace_flow,
)
from .run import AdaptationRun, CheckpointStore, RunJournal

ResourceKind = Literal["skill", "context", "hook"]
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
    resource_kind: ResourceKind
    resource_name: str
    section: str | None = None
    delta: Ranks = Field(default_factory=Ranks)
    reason: ChangeReason


class RankOperation(BaseModel):
    """A curator-approved rank update."""

    resource_kind: ResourceKind
    resource_name: str
    section: str | None = None
    delta: Ranks = Field(default_factory=Ranks)
    reason: ChangeReason


class JournalEntry(BaseModel):
    run_id: str
    change_id: str
    resource_kind: ResourceKind
    resource_name: str
    section: str | None = None
    operation: str = "increment_rank"
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
        LOGGER.info("journaled %s change for %s:%s", entry.change_id, entry.resource_kind, entry.resource_name)

    def entries(self) -> list[JournalEntry]:
        if not self.path.exists():
            return []
        return [
            JournalEntry.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line
        ]


class ReasonedResourceStore:
    """Interface for mutations that cannot be made without a reason."""

    def update_skill(self, name: str, delta: Ranks, *, reason: ChangeReason) -> tuple[str, str]:
        raise NotImplementedError

    def update_context(self, filename: str, section: str, delta: Ranks, *, reason: ChangeReason) -> tuple[str, str]:
        raise NotImplementedError

    def update_hook(self, name: str, delta: Ranks, *, reason: ChangeReason) -> tuple[str, str]:
        raise NotImplementedError


class FileResourceStore(ReasonedResourceStore):
    """Toy store for skill Markdown, context Markdown, and hook JSON files."""

    def __init__(
        self,
        skills: dict[str, str | Path],
        contexts: dict[str, str | Path],
        hooks: dict[str, str | Path],
    ):
        self.skills = {name: Path(path).expanduser() for name, path in skills.items()}
        self.contexts = {name: Path(path).expanduser() for name, path in contexts.items()}
        self.hooks = {name: Path(path).expanduser() for name, path in hooks.items()}

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _add(before: Ranks | None, delta: Ranks) -> Ranks:
        before = before or Ranks()
        return Ranks(
            harmful=before.harmful + delta.harmful,
            neutral=before.neutral + delta.neutral,
            helpful=before.helpful + delta.helpful,
        )

    def load_context(self) -> AceContext:
        skills = {}
        for name, path in self.skills.items():
            definition = SkillDefinition.from_markdown(name, path.read_text(encoding="utf-8"))
            skills[name] = Skill(
                name=definition.metadata.name,
                description=definition.metadata.description,
                instructions=definition.instructions,
                ranks=definition.metadata.ranks,
                metadata=definition.metadata.model_dump(exclude={"ranks"}),
            )
        contexts = {name: ContextDocument.from_file(path) for name, path in self.contexts.items()}
        hooks = {name: Hook.model_validate_json(path.read_text(encoding="utf-8")) for name, path in self.hooks.items()}
        return AceContext(skills={"skills": skills}, context={"documents": contexts}, hooks={"hooks": hooks})

    def update_skill(self, name: str, delta: Ranks, *, reason: ChangeReason) -> tuple[str, str]:
        if not reason.summary:
            raise ValueError("reason is required")
        path = self.skills[name]
        before = path.read_text(encoding="utf-8")
        definition = SkillDefinition.from_markdown(name, before)
        definition.metadata.ranks = self._add(definition.metadata.ranks, delta)
        after = definition.to_markdown()
        path.write_text(after, encoding="utf-8")
        return self._hash(before), self._hash(after)

    def update_context(self, filename: str, section: str, delta: Ranks, *, reason: ChangeReason) -> tuple[str, str]:
        if not reason.summary:
            raise ValueError("reason is required")
        path = self.contexts[filename]
        before = path.read_text(encoding="utf-8")
        document = ContextDocument.from_markdown(filename, before)
        markdown = document.to_markdown()
        parsed = __import__("thearc.models", fromlist=["MarkdownDocument"]).MarkdownDocument.parse(markdown)
        target = parsed.get_header(section, exact=True)
        if target is None:
            raise KeyError(f"context section not found: {filename}:{section}")
        target.ranks = self._add(target.ranks, delta)
        after = parsed.to_markdown()
        path.write_text(after, encoding="utf-8")
        return self._hash(before), self._hash(after)

    def update_hook(self, name: str, delta: Ranks, *, reason: ChangeReason) -> tuple[str, str]:
        if not reason.summary:
            raise ValueError("reason is required")
        path = self.hooks[name]
        before = path.read_text(encoding="utf-8")
        hook = Hook.model_validate_json(before)
        hook.ranks = self._add(hook.ranks, delta)
        after = json.dumps(hook.to_dict(), indent=2, sort_keys=True) + "\n"
        path.write_text(after, encoding="utf-8")
        return self._hash(before), self._hash(after)


class ToyReflector:
    """Generate rank proposals from events while explicitly hiding prior ranks."""

    def reflect(self, batch: Sequence[SessionBundle], target: AceContext) -> Reflection:
        # The reflector receives a rank-free projection. This is the boundary
        # that prevents historical scores from biasing the observation.
        rank_free = target.model_copy(deep=True)
        for skill in rank_free.skills:
            skill.ranks = None
        for hook in rank_free.hooks:
            hook.ranks = None
        for document in rank_free.context:
            parsed = __import__("thearc.models", fromlist=["MarkdownDocument"]).MarkdownDocument.parse(document.content)
            document.content = parsed.to_markdown(include_ranks=False)

        failures = [event for bundle in batch for event in bundle.events if event.status == "failure"]
        successes = [event for bundle in batch for event in bundle.events if event.status == "success"]
        LOGGER.info("reflecting %d sessions: %d successes, %d failures", len(batch), len(successes), len(failures))
        evidence = [event.id for event in failures[:3] + successes[:3]]
        proposals: list[RankProposal] = []
        if rank_free.skills:
            name = next(iter(rank_free.skills)).name
            proposals.append(
                RankProposal(
                    resource_kind="skill",
                    resource_name=name,
                    delta=Ranks(harmful=len(failures), helpful=len(successes)),
                    reason=ChangeReason(
                        summary="Toy reflector counted execution outcomes in the session batch",
                        evidence_event_ids=evidence,
                    ),
                )
            )
        if rank_free.context:
            document = next(iter(rank_free.context))
            from thearc.models import MarkdownDocument

            parsed = MarkdownDocument.parse(document.content)
            if parsed.sections:
                proposals.append(
                    RankProposal(
                        resource_kind="context",
                        resource_name=document.filename,
                        section=parsed.sections[0].title,
                        delta=Ranks(harmful=len(failures), helpful=len(successes)),
                        reason=ChangeReason(
                            summary="Toy reflector scored the first context section", evidence_event_ids=evidence
                        ),
                    )
                )
        if rank_free.hooks:
            name = next(iter(rank_free.hooks)).name
            proposals.append(
                RankProposal(
                    resource_kind="hook",
                    resource_name=name,
                    delta=Ranks(harmful=len(failures), helpful=len(successes)),
                    reason=ChangeReason(
                        summary="Toy reflector scored hook-related execution outcomes", evidence_event_ids=evidence
                    ),
                )
            )
        return Reflection(
            id=f"reflection-{len(batch)}-{len(evidence)}",
            session_ids=[bundle.session.id for bundle in batch],
            observations=[f"failures={len(failures)}", f"successes={len(successes)}"],
            evidence_event_ids=evidence,
            rank_proposals=[proposal.model_dump() for proposal in proposals],
        )


class ToyCurator:
    """Accept proposals using current ranks, which the reflector never sees."""

    def __init__(self, target: AceContext):
        self.target = target
        self.seen_ranks: list[dict[str, Any]] = []

    def curate(self, batch: Sequence[Reflection], playbook: Playbook) -> Curation:
        operations: list[dict[str, Any]] = []
        observed: dict[str, dict[str, int]] = {}
        for reflection in batch:
            for raw in reflection.rank_proposals:
                proposal = RankProposal.model_validate(raw)
                current = self._current(proposal)
                observed[f"{proposal.resource_kind}:{proposal.resource_name}:{proposal.section}"] = current.model_dump()
                # Toy policy: keep helpful evidence, but suppress new harmful
                # votes once historical harmful votes substantially dominate.
                if proposal.delta.harmful and current.harmful > current.helpful + 2:
                    continue
                operations.append(
                    RankOperation(
                        **proposal.model_dump(exclude={"reason"}),
                        reason=ChangeReason(
                            summary=f"Curator accepted proposal with current ranks {current.model_dump()}",
                            evidence_event_ids=proposal.reason.evidence_event_ids,
                            reflection_ids=[reflection.id],
                            actor="ace-toy-curator",
                        ),
                    ).model_dump()
                )
        self.seen_ranks.append(observed)
        LOGGER.info("curated %d reflections: accepted %d rank operations", len(batch), len(operations))
        return Curation(
            id=f"curation-{len(batch)}-{len(operations)}",
            reflection_ids=[reflection.id for reflection in batch],
            operations=operations,
            rationale="Toy curator used current rank counters to gate harmful updates.",
        )

    def _current(self, proposal: RankProposal) -> Ranks:
        if proposal.resource_kind == "skill":
            return self.target.skills[proposal.resource_name].ranks or Ranks()
        if proposal.resource_kind == "hook":
            return self.target.hooks[proposal.resource_name].ranks or Ranks()
        document = self.target.context[proposal.resource_name]
        from thearc.models import MarkdownDocument

        section = MarkdownDocument.parse(document.content).get_header(proposal.section or "", exact=True)
        return section.ranks if section and section.ranks else Ranks()


class ToyUpdater:
    """Apply curator operations and journal every file mutation."""

    def __init__(self, store: FileResourceStore, journal: ChangeJournal, run_id: str = "ace-toy"):
        self.store = store
        self.journal = journal
        self.run_id = run_id

    def update(self, playbook: Playbook, curations: Sequence[Curation]) -> UpdateResult:
        applied: list[str] = []
        rejected: list[str] = []
        for curation in curations:
            for index, raw in enumerate(curation.operations):
                operation = RankOperation.model_validate(raw)
                try:
                    before, after = self._apply(operation)
                except (KeyError, OSError, ValueError):
                    rejected.append(f"{curation.id}:{index}")
                    LOGGER.warning("rejected operation %s:%d", curation.id, index)
                    continue
                change_id = f"{curation.id}:{index}"
                self.journal.append(
                    JournalEntry(
                        run_id=self.run_id,
                        change_id=change_id,
                        resource_kind=operation.resource_kind,
                        resource_name=operation.resource_name,
                        section=operation.section,
                        before_sha256=before,
                        after_sha256=after,
                        reason=operation.reason,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                applied.append(change_id)
                LOGGER.info("applied and journaled %s", change_id)
        return UpdateResult(
            playbook_id=playbook.id,
            previous_revision=playbook.revision,
            new_revision=playbook.revision + bool(applied),
            applied_curation_ids=applied,
            rejected_curation_ids=rejected,
        )

    def _apply(self, operation: RankOperation) -> tuple[str, str]:
        if operation.resource_kind == "skill":
            return self.store.update_skill(operation.resource_name, operation.delta, reason=operation.reason)
        if operation.resource_kind == "context":
            if not operation.section:
                raise ValueError("context rank updates require a section")
            return self.store.update_context(
                operation.resource_name, operation.section, operation.delta, reason=operation.reason
            )
        return self.store.update_hook(operation.resource_name, operation.delta, reason=operation.reason)


def run_toy_flow(
    *,
    selector: Any,
    materializer: Any,
    target: AceContext,
    store: FileResourceStore,
    journal: ChangeJournal,
    playbook: Playbook,
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
    from thearc.flow.viz import FlowTracker

    updater = ToyUpdater(store, journal, run_id=run_id)
    lifecycle = AdaptationRun(
        run_id,
        flow_name="ace-toy-flow",
        flow_version="1",
        journal=run_journal,
        checkpoint=CheckpointStore(checkpoint_path) if checkpoint_path else None,
    )
    shared = {
        "selector": selector,
        "materializer": materializer,
        "reflector": ToyReflector(),
        "curator": ToyCurator(target),
        "updater": updater,
        "config": config or AceConfig(),
        "query": query,
        "target": target,
        "playbook": playbook,
    }
    flow = flow or build_ace_flow()
    with lifecycle:
        if viz_path is None:
            flow.run(shared)
        else:
            FlowTracker(flow, output=str(viz_path), refresh_interval=refresh_interval).run(shared)
        result = UpdateResult.model_validate(shared["update"])
        lifecycle.complete(result.model_dump(), node="update")
        return result
