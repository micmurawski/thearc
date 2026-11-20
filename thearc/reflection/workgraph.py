"""Opt-in experiment: reflect over a compact WorkGraph with source-event lookup."""

from __future__ import annotations

from typing import Any

from thearc.resources.agent import AgentConfig
from thearc.resources.reflection import ReflectionSchema
from thearc.sessions.analysis import WorkGraph
from thearc.sessions.session import EventSequence, Trajectory

from .reflector import Reflection, Reflector


class WorkGraphReflector:
    """Run a fresh reflection from a compact trajectory graph, with bounded source expansion.

    Unlike ``Reflector.handoff``, this experimental path does not fork the full
    original conversation. The graph is the initial evidence; the reflector can
    request selected redacted source events over the existing reflection MCP.
    """

    def __init__(self, reflector: Reflector | None = None):
        self.reflector = reflector or Reflector("claude")

    def reflect(
        self,
        prompt: str,
        history: EventSequence,
        agent: AgentConfig,
        schema: ReflectionSchema | Any,
        *,
        provenance: str | None = None,
        **extra: Any,
    ) -> Reflection:
        """Reflect on a graph projection while preserving source-event lookup."""
        if isinstance(history, Trajectory):
            graph = WorkGraph.from_trajectory(history)
        else:
            graph = WorkGraph.from_events(
                history.selected_events,
                session_id=getattr(history, "session_id", getattr(history, "id", None)),
            )
        evidence = graph.to_markdown()
        if provenance:
            evidence += f"\n\n## How the session ran\n\n{provenance}"
        evidence += (
            "\n\nThe graph is a deterministic projection, not an interpretation. "
            "When a conclusion depends on omitted detail, call `expand_source_events` with the relevant event IDs. "
            "Treat the returned source events as evidence; do not infer causes from graph edges alone."
        )
        graph_record = {
            "trajectory_id": graph.trajectory_id,
            "session_id": graph.session_id,
            "source_events": graph.source_event_count,
            "omitted_events": graph.omitted_event_count,
            "visible_events": sum(node.kind != "signal" for node in graph.nodes),
            "signals": sum(node.kind == "signal" for node in graph.nodes),
            "redacted": graph.redacted,
            "overview": graph.overview.to_dict(),
        }
        return self.reflector.reflect_with_tools(
            prompt,
            agent,
            schema,
            evidence=evidence,
            mcp_tools=(graph.expansion_tool(),),
            work_graph=graph_record,
            **extra,
        )
