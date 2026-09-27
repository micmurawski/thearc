"""Shared ACE orchestration and contracts for reflection and optional curation.

Model-backed reflection lives in codex_reflector; approval, installation, and
production curation remain application choices.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator

from thearc.models.agent import MetaAgent, Operation, ResourceTarget

from .evaluation import EvaluationResult, TaskScore, compare_agents
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
    omitted_event_ids: list[str] = Field(default_factory=list)
    truncated_event_ids: list[str] = Field(default_factory=list)


class ReflectionEvidence(BaseModel):
    model_config = {"extra": "forbid"}
    session_id: str
    event_id: str


class ReflectionRating(str, Enum):
    HELPFUL = "helpful"
    NEUTRAL = "neutral"
    HARMFUL = "harmful"


class ReflectionItem(BaseModel):
    """An evidence-backed rating of one specific configuration resource/section."""

    model_config = {"extra": "forbid"}
    target: ResourceTarget
    rating: ReflectionRating
    reason: str = Field(min_length=1)
    evidence: list[ReflectionEvidence]
    limitations: list[str]


class Reflection(BaseModel):
    """A reflector's observations; it is not yet a MetaAgent update."""

    id: str
    session_ids: list[str]
    summary: str = ""
    items: list[ReflectionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    successful_patterns: list[str] = Field(default_factory=list)
    failure_patterns: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    rank_proposals: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Curation(BaseModel):
    """One proposed operation on a resource or Markdown section of a MetaAgent."""

    id: str
    target: ResourceTarget
    operation: Operation
    value: dict[str, Any] | str | None = None
    reflection_ids: list[str]
    evidence_event_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_value(self) -> Curation:
        if self.operation == Operation.REMOVE and self.value is not None:
            raise ValueError("REMOVE does not accept a value")
        if self.operation != Operation.REMOVE and self.value is None:
            raise ValueError("ADD and EDIT require a value")
        return self


class UpdateResult(BaseModel):
    """The adapted MetaAgent and the changes accepted or rejected by the updater."""

    agent: MetaAgent
    applied_curation_ids: list[str] = Field(default_factory=list)
    rejected_curation_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SessionSelector(Protocol):
    def select(self, query: AceQuery | None = None) -> Sequence[Session]: ...


class SessionMaterializer(Protocol):
    def materialize(self, session: Session, config: AceConfig) -> SessionBundle: ...


class Reflector(Protocol):
    def reflect(self, batch: Sequence[SessionBundle], agent: MetaAgent) -> Reflection: ...


class Curator(Protocol):
    def curate(self, batch: Sequence[Reflection], agent: MetaAgent) -> Sequence[Curation]: ...


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

    def materialize(self, session: Session | dict[str, Any], config: AceConfig) -> SessionBundle:
        if not isinstance(session, Session):
            session = Session.model_validate(session)
        events = list(
            self.history.iter_events(
                SearchFilters(session_ids=[session.id]),
            )
        )
        selected = select_evidence_events(events, config.max_events_per_session)
        selected_ids = {event.id for event in selected}
        truncated = [event.id for event in selected if len(event.text) > config.max_text_chars_per_event]
        if config.max_text_chars_per_event:
            selected = [
                event.model_copy(update={"text": event.text[: config.max_text_chars_per_event]}) for event in selected
            ]
        return SessionBundle(
            session=session, events=selected,
            omitted_event_ids=[event.id for event in events if event.id not in selected_ids],
            truncated_event_ids=truncated,
        )


def select_evidence_events(events: Sequence[Event], limit: int) -> list[Event]:
    """Keep task/outcome first, then tool groups; never split a known call group."""
    groups: dict[tuple[str, str], list[int]] = {}
    for index, event in enumerate(events):
        key = (event.run_id, event.call_id) if event.call_id else ("event", str(index))
        groups.setdefault(key, []).append(index)
    priority = []
    users = [i for i, event in enumerate(events) if event.role == "user" and not event.call_id]
    assistants = [i for i, event in enumerate(events)
                  if event.role == "assistant" and event.kind == "message" and not event.call_id]
    if users:
        priority.append(users[0])
    if assistants:
        priority.append(assistants[-1])
    priority.extend(range(len(events)))
    by_index = {i: group for group in groups.values() for i in group}
    chosen: set[int] = set()
    for index in priority:
        group = set(by_index[index])
        if len(chosen | group) <= limit:
            chosen.update(group)
    return [event for i, event in enumerate(events) if i in chosen]


def chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class AceRunResult(BaseModel):
    run_id: str
    sessions: list[Session]
    reflections: list[Reflection]
    curations: list[Curation]
    update: UpdateResult
    evaluation: EvaluationResult | None = None

    @property
    def agent(self) -> MetaAgent:
        """The adapted configuration, ready for evaluation or installation."""
        return self.update.agent


def apply_curations(
    candidate: MetaAgent,
    curations: Sequence[Curation],
    on_change: Callable[[Curation, Any, Any], None] | None = None,
) -> UpdateResult:
    """Validate and apply each change once to the caller-owned candidate.

    The pipeline creates this candidate from a copy of its input. Recording
    happens only after a successful change; recorder failures abort the run.
    """
    result = UpdateResult(agent=candidate)
    for change in curations:
        try:
            if not change.rationale.strip():
                raise ValueError("a non-blank rationale is required")
            before, after = candidate.apply_change(change.target, change.operation, change.value)
        except (KeyError, ValueError) as exc:
            result.rejected_curation_ids.append(change.id)
            result.warnings.append(f"{change.id}: {exc}")
            continue
        if on_change is not None:
            on_change(change, before, after)
        result.applied_curation_ids.append(change.id)
    return result


class AcePipeline:
    """Run the ACE flow, optionally visualizing and evaluating its candidate."""

    def __init__(
        self,
        selector: SessionSelector,
        materializer: SessionMaterializer,
        reflector: Reflector,
        curator: Curator | None = None,
        config: AceConfig | None = None,
        *,
        on_change: Callable[[Curation, Any, Any], None] | None = None,
    ):
        self.selector = selector
        self.materializer = materializer
        self.reflector = reflector
        self.curator = curator
        self.config = config or AceConfig()
        self.on_change = on_change

    def run(
        self,
        agent: MetaAgent,
        query: AceQuery | None = None,
        *,
        run_id: str | None = None,
        viz_path: str | None = None,
        refresh_interval: float = 1.0,
        flow: Any = None,
        evaluation_tasks: Mapping[str, Any] | None = None,
        evaluator: Callable[[MetaAgent, Any], TaskScore] | None = None,
        reflection_only: bool = False,
    ) -> AceRunResult:
        if (evaluation_tasks is None) != (evaluator is None):
            raise ValueError("supply both evaluation_tasks and evaluator")
        if reflection_only and (evaluator is not None or flow is not None):
            raise ValueError("reflection-only runs do not accept evaluators or custom flows")
        if not reflection_only and self.curator is None:
            raise ValueError("a curator is required unless reflection_only=True")
        run_id = run_id or datetime.now(timezone.utc).strftime("ace-%Y%m%dT%H%M%S.%fZ")
        shared = {
            "selector": self.selector, "materializer": self.materializer,
            "reflector": self.reflector, "curator": self.curator,
            "config": self.config, "query": query, "agent": agent,
            "on_change": self.on_change,
        }
        flow = flow or build_ace_flow(reflection_only=reflection_only)
        if viz_path is None:
            flow.run(shared)
        else:
            from thearc.flow.viz import FlowTracker

            FlowTracker(flow, output=str(viz_path), refresh_interval=refresh_interval).run(shared)
        result = AceRunResult(
            run_id=run_id,
            sessions=shared["sessions"], reflections=shared["reflections"],
            curations=shared["curations"], update=shared["update"],
        )
        if evaluator is not None:
            result.evaluation = compare_agents(
                agent, result.agent, evaluation_tasks, evaluator,
                training_task_ids=[session.id for session in result.sessions],
            )
        return result


def build_ace_flow(*, reflection_only: bool = False):
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
    def reflect_batch(batch: list[Any], reflector: Any, agent: Any, log: Any = None) -> Any:
        result = reflector.reflect(
            [item if isinstance(item, SessionBundle) else SessionBundle.model_validate(item) for item in batch],
            (agent if isinstance(agent, MetaAgent) else MetaAgent.model_validate(agent)).without_ranks(),
        )
        _log(log, "info", f"ACE reflected a batch of {len(batch)} sessions")
        return result

    @node(metadata={"stage": "batch", "description": "Group exactly K reflections per curation call"})
    def group_reflections(reflections: list[Any], config: Any, log: Any = None) -> dict:
        batches = [{"batch": list(batch)} for batch in chunks(reflections, config.reflections_per_curation)]
        _log(log, "info", f"ACE grouped {len(reflections)} reflections into {len(batches)} curation batches")
        return {"curation_batches": batches}

    @node(metadata={"stage": "curation", "description": "Curate, apply, and record each change once"})
    def curate_and_apply(
        curation_batches: list[Any], curator: Any, agent: Any,
        on_change: Any = None, log: Any = None,
    ) -> dict:
        source = agent if isinstance(agent, MetaAgent) else MetaAgent.model_validate(agent)
        candidate = source.model_copy(deep=True)
        result = UpdateResult(agent=candidate)
        curations = []
        for batch in curation_batches:
            reflections = [Reflection.model_validate(item) for item in batch["batch"]]
            proposed = list(curator.curate(reflections, candidate.model_copy(deep=True)))
            curations.extend(proposed)
            applied = apply_curations(candidate, proposed, on_change)
            result.applied_curation_ids.extend(applied.applied_curation_ids)
            result.rejected_curation_ids.extend(applied.rejected_curation_ids)
            result.warnings.extend(applied.warnings)
        _log(log, "info", f"ACE applied {len(result.applied_curation_ids)} MetaAgent changes")
        return {"curations": curations, "update": result, "agent": candidate}

    @node(metadata={"stage": "complete", "description": "Return reflections without configuration changes"})
    def finish_reflections(agent: Any) -> dict:
        source = agent if isinstance(agent, MetaAgent) else MetaAgent.model_validate(agent)
        return {"curations": [], "update": UpdateResult(agent=source.model_copy(deep=True))}

    if reflection_only:
        query_sessions >> materialize_sessions >> group_sessions >> reflect_batch >> finish_reflections
        return Flow(start=query_sessions)

    (
        query_sessions
        >> materialize_sessions
        >> group_sessions
        >> reflect_batch
        >> group_reflections
        >> curate_and_apply
    )
    return Flow(start=query_sessions)
