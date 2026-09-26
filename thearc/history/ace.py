"""A deliberately incomplete ACE-style history adaptation pipeline.

This module defines orchestration and data contracts only. Provider adapters,
LLM calls, ranking, durable playbook storage, and update approval remain
application-specific TODOs.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field

from thearc.models.agent import ContextSet, HookSet, SkillSet

from .models import Event, SearchFilters, Session
from .service import HistoryService

LOGGER = logging.getLogger("thearc.ace")


def _log(log: Any, level: str, message: str) -> None:
    (log or LOGGER).__getattribute__(level)(message)


class AceConfig(BaseModel):
    """Batching and evidence limits for one adaptation run."""

    sessions_per_reflection: int = Field(default=10, ge=1)
    reflections_per_curation: int = Field(default=10, ge=1)
    max_events_per_session: int = Field(default=200, ge=1)
    max_text_chars_per_event: int = Field(default=4000, ge=1)

    # TODO: add a token budget and a policy for truncating by relevance rather
    # than by event order once a tokenizer is selected.


class AceQuery(BaseModel):
    """Future session-selection contract; execution of time/tags is TODO."""

    start_at: str | None = None
    end_at: str | None = None
    tags: list[str] = Field(default_factory=list)
    filters: SearchFilters = Field(default_factory=SearchFilters)


class SessionBundle(BaseModel):
    """A bounded, immutable input passed to the reflection stage."""

    session: Session
    events: list[Event] = Field(default_factory=list)


class AceContext(BaseModel):
    """Skills, context documents, and hooks evaluated by a reflection run."""

    skills: SkillSet = Field(default_factory=SkillSet)
    context: ContextSet = Field(default_factory=ContextSet)
    hooks: HookSet = Field(default_factory=HookSet)

    def without_ranks(self) -> AceContext:
        """Return the reflector view with historical rank metadata removed."""
        from thearc.models import MarkdownDocument

        view = self.model_copy(deep=True)
        for skill in view.skills:
            skill.ranks = None
        for hook in view.hooks:
            hook.ranks = None
        for document in view.context:
            document.content = MarkdownDocument.parse(document.content).to_markdown(include_ranks=False)
        return view


class Reflection(BaseModel):
    """A reflector's observations; it is not yet a playbook update."""

    id: str
    session_ids: list[str]
    observations: list[str] = Field(default_factory=list)
    successful_patterns: list[str] = Field(default_factory=list)
    failure_patterns: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    rank_proposals: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Curation(BaseModel):
    """A proposed incremental change to the playbook."""

    id: str
    reflection_ids: list[str]
    operations: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class Playbook(BaseModel):
    """The current itemized context owned by an external persistence layer."""

    id: str
    revision: int = 0
    bullets: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateResult(BaseModel):
    playbook_id: str
    previous_revision: int
    new_revision: int
    applied_curation_ids: list[str] = Field(default_factory=list)
    rejected_curation_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SessionSelector(Protocol):
    def select(self, query: AceQuery | None = None) -> Sequence[Session]: ...


class SessionMaterializer(Protocol):
    def materialize(self, session: Session, config: AceConfig) -> SessionBundle: ...


class Reflector(Protocol):
    def reflect(self, batch: Sequence[SessionBundle], target: AceContext) -> Reflection: ...


class Curator(Protocol):
    def curate(self, batch: Sequence[Reflection], playbook: Playbook) -> Curation: ...


class PlaybookUpdater(Protocol):
    """The single final update node; it must receive all curations in a run."""

    def update(self, playbook: Playbook, curations: Sequence[Curation]) -> UpdateResult: ...


class HistorySessionSelector:
    def __init__(self, history: HistoryService):
        self.history = history

    def select(self, query: AceQuery | None = None) -> Sequence[Session]:
        # TODO: add cursor/snapshot selection when adaptation runs must be
        # reproducible while another process is syncing the index.
        # TODO: push start_at/end_at/tags into the indexed session query.
        return self.history.list_sessions(query.filters if query else None, limit=1000)


class HistorySessionMaterializer:
    def __init__(self, history: HistoryService):
        self.history = history

    def materialize(self, session: Session, config: AceConfig) -> SessionBundle:
        events = list(
            self.history.iter_events(
                SearchFilters(session_ids=[session.id]),
            )
        )
        events = events[: config.max_events_per_session]
        if config.max_text_chars_per_event:
            events = [
                event.model_copy(update={"text": event.text[: config.max_text_chars_per_event]}) for event in events
            ]
        # TODO: select events by task relevance and preserve tool-call/result
        # pairs instead of using chronological truncation.
        return SessionBundle(session=session, events=events)


def chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass
class AceRunResult:
    run_id: str
    sessions: list[Session]
    reflections: list[Reflection]
    curations: list[Curation]
    update: UpdateResult


class AcePipeline:
    """Coordinates bounded fan-out and one final playbook update.

    The pipeline intentionally has no concurrency or retry policy yet. Those
    policies affect cost, ordering, and idempotency and should be chosen after
    the model contracts are settled.
    """

    def __init__(
        self,
        selector: SessionSelector,
        materializer: SessionMaterializer,
        reflector: Reflector,
        curator: Curator,
        updater: PlaybookUpdater,
        config: AceConfig | None = None,
    ):
        self.selector = selector
        self.materializer = materializer
        self.reflector = reflector
        self.curator = curator
        self.updater = updater
        self.config = config or AceConfig()

    def run(
        self,
        playbook: Playbook,
        target: AceContext,
        query: AceQuery | None = None,
    ) -> AceRunResult:
        run_id = datetime.now(timezone.utc).strftime("ace-%Y%m%dT%H%M%S.%fZ")
        sessions = list(self.selector.select(query))
        bundles = [self.materializer.materialize(session, self.config) for session in sessions]

        reflection_target = target.without_ranks()
        reflections = [
            self.reflector.reflect(batch, reflection_target)
            for batch in chunks(bundles, self.config.sessions_per_reflection)
        ]
        curations = [
            self.curator.curate(batch, playbook) for batch in chunks(reflections, self.config.reflections_per_curation)
        ]

        # Deliberately one call: the updater is the single node that can mutate
        # the playbook after seeing every curation produced by this run.
        update = self.updater.update(playbook, curations)
        return AceRunResult(run_id, sessions, reflections, curations, update)


def build_ace_flow():
    """Build a visualizable ``thearc.flow`` graph for the ACE stages."""
    from thearc.flow import Flow
    from thearc.flow.decorators import node

    @node(metadata={"stage": "query", "description": "Query sessions by time range and tags"})
    def query_sessions(selector: Any, query: Any = None, log: Any = None) -> dict:
        sessions = list(selector.select(query))
        _log(log, "info", f"ACE query selected {len(sessions)} sessions")
        return {"sessions": sessions}

    @node(metadata={"stage": "materialize", "description": "Load bounded evidence for each session"})
    def materialize_sessions(sessions: list[Any], materializer: Any, config: Any, log: Any = None) -> dict:
        bundles = [materializer.materialize(session, config) for session in sessions]
        _log(log, "info", f"ACE materialized {len(bundles)} session bundles")
        return {"bundles": bundles}

    @node(metadata={"stage": "batch", "description": "Group exactly N sessions per reflection call"})
    def group_sessions(bundles: list[Any], config: Any, log: Any = None) -> dict:
        batches = [{"batch": list(batch)} for batch in chunks(bundles, config.sessions_per_reflection)]
        _log(log, "info", f"ACE grouped {len(bundles)} sessions into {len(batches)} reflection batches")
        return {"reflection_batches": batches}

    @node(batch=True, items_key="reflection_batches", results_key="reflections", metadata={"stage": "reflection"})
    def reflect_batch(batch: list[Any], reflector: Any, target: Any, log: Any = None) -> Any:
        result = reflector.reflect(
            [item if isinstance(item, SessionBundle) else SessionBundle.model_validate(item) for item in batch],
            (target if isinstance(target, AceContext) else AceContext.model_validate(target)).without_ranks(),
        )
        _log(log, "info", f"ACE reflected a batch of {len(batch)} sessions")
        return result

    @node(metadata={"stage": "batch", "description": "Group exactly K reflections per curation call"})
    def group_reflections(reflections: list[Any], config: Any, log: Any = None) -> dict:
        batches = [{"batch": list(batch)} for batch in chunks(reflections, config.reflections_per_curation)]
        _log(log, "info", f"ACE grouped {len(reflections)} reflections into {len(batches)} curation batches")
        return {"curation_batches": batches}

    @node(batch=True, items_key="curation_batches", results_key="curations", metadata={"stage": "curation"})
    def curate_batch(batch: list[Any], curator: Any, playbook: Any, target: Any, log: Any = None) -> Any:
        result = curator.curate(
            [item if isinstance(item, Reflection) else Reflection.model_validate(item) for item in batch],
            playbook if isinstance(playbook, Playbook) else Playbook.model_validate(playbook),
        )
        _log(log, "info", f"ACE curated a batch of {len(batch)} reflections")
        return result

    @node(metadata={"stage": "update", "description": "Single update node receives all curations"})
    def update_playbook(playbook: Any, curations: list[Any], updater: Any, log: Any = None) -> dict:
        result = {
            "update": updater.update(
                playbook if isinstance(playbook, Playbook) else Playbook.model_validate(playbook),
                [item if isinstance(item, Curation) else Curation.model_validate(item) for item in curations],
            )
        }
        _log(log, "info", f"ACE update processed {len(curations)} curations")
        return result

    (
        query_sessions
        >> materialize_sessions
        >> group_sessions
        >> reflect_batch
        >> group_reflections
        >> curate_batch
        >> update_playbook
    )
    return Flow(start=query_sessions)
