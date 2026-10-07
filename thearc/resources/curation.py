"""Schema-driven curation backed by the existing candidate/epoch validation engine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from thearc.learning.curation.engine import AgentCurator, run_curation
from thearc.learning.curation.models import AssessmentReport
from thearc.learning.evidence.snapshot import content_hash
from thearc.models import MetaAgent

from .immutable import Record, freeze, thaw
from .reflection import Reflection, Reflector
from .schemas import CurationReport, schema_for


class EvidenceTools:
    """Bounded access to the immutable scopes already attached to reflections."""

    def __init__(self, histories):
        self.histories = histories
        self.calls = 0

    @property
    def definitions(self):
        return [{"type": "function", "name": "arc_evidence",
                 "description": ("Read an event from captured history. Empty event_id lists event IDs; "
                                 "offset selects list entries or characters in a single event's Markdown."),
                 "inputSchema": {"type": "object", "additionalProperties": False,
                                 "properties": {"reflection_id": {"type": "string"},
                                                "event_id": {"type": "string"},
                                                "offset": {"type": "integer", "minimum": 0}},
                                 "required": ["reflection_id", "event_id", "offset"]}}]

    def call(self, name, arguments):
        self.calls += 1
        if self.calls > 100 or name != "arc_evidence" or set(arguments) != {"reflection_id", "event_id", "offset"}:
            raise ValueError("Unknown evidence operation or exhausted call budget")
        history = self.histories.get(arguments["reflection_id"])
        if history is None:
            raise ValueError("No captured history is attached to this reflection")
        offset = arguments["offset"]
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid offset")
        events = history.selected_events
        if arguments["event_id"] == "":
            return {"events": [{"event_id": e.id, "kind": e.kind, "role": e.role}
                               for e in events[offset:offset + 50]],
                    "next_offset": offset + 50 if offset + 50 < len(events) else None}
        event = next((e for e in events if e.id == arguments["event_id"]), None)
        if event is None:
            raise ValueError("Event is outside this reflection's selected history")
        from .formats import evidence_to_markdown

        text = evidence_to_markdown([event])
        if offset >= len(text):
            raise ValueError("Offset is outside the event")
        end = offset + 16_000
        return {"content": text[offset:end], "total_chars": len(text),
                "next_offset": end if end < len(text) else None}


@dataclass(frozen=True)
class CurationResult:
    _agent_json: str
    changes: tuple
    summary: str
    output: object
    epoch: object

    @property
    def agent(self):
        return MetaAgent.model_validate_json(self._agent_json)


class Curator:
    def __init__(self, *, implementation, model, runner=None, artifact_dir=None, **settings):
        self.reflector = Reflector(implementation=implementation, model=model, runner=runner,
                                   artifact_dir=artifact_dir, **settings)

    def curate(self, prompt, reflections, *, agent, output_schema=CurationReport, validate_resources=True,
               adapter=None, output=None, result_version=None):
        """Return a detached candidate; `output=` additionally commits a durable epoch.

        Reflection.assessments(history) explicitly verifies event citations before
        rank propagation. Text/custom outputs contribute observations, never ratings.
        """
        if not isinstance(agent, MetaAgent):
            raise TypeError("agent must be a MetaAgent")
        schema_snapshot = schema_for(output_schema)  # Validate before workspace creation or inference.
        if output_schema is not CurationReport and adapter is None:
            raise ValueError("Custom curation schemas require an explicit adapter to CurationReport")
        baseline = agent.model_copy(deep=True)
        reflections = tuple(reflections)
        histories = {r.id: r.history for r in reflections if isinstance(r, Reflection) and r.history is not None}
        findings = []
        for reflection in reflections:
            if isinstance(reflection, Reflection):
                findings.append(AssessmentReport(
                    id=reflection.id, session_ids=[], summary=reflection.text,
                    raw={"manifest": thaw(reflection.provenance)},
                ))
            elif isinstance(reflection, AssessmentReport):
                findings.append(reflection.model_copy(deep=True))
            else:
                raise TypeError("Supply Reflection objects or validated assessment artifacts")
        if len({r.id for r in findings}) != len(findings):
            raise ValueError("Duplicate reflection identity")
        typed_output = None

        def run(engine_prompt, schema, tools):
            nonlocal typed_output
            catalog = tools.call("arc_list", {})
            resources, history = [], []
            for item in catalog:
                target = json.dumps(item["target"])
                for tool, destination in (("arc_read", resources), ("arc_history", history)):
                    offset = 0
                    while offset is not None:
                        page = tools.call(tool, {"target_json": target, "offset": offset})
                        if tool == "arc_read":
                            # The reflector supplies the complete rank-free catalog once.
                            if offset == 0:
                                destination.append({"id": item["id"], "target": item["target"],
                                                    "editable": item["editable"],
                                                    "lifetime_ranks": page["lifetime_ranks"],
                                                    "current_ranks": page["current_ranks"]})
                        else:
                            destination.append({"target": item["target"], "page": page})
                        offset = page["next_offset"]
            if not catalog:
                # Empty configuration has no resources or resource history to page.
                tools.inspections.update({"resource", "history"})
            request = (prompt + "\n\nREFLECTIONS:\n" +
                       json.dumps([{"id": r.id, "summary": r.summary,
                                    "items": [i.model_dump(mode="json") for i in r.items],
                                    "limitations": r.limitations,
                                    "agent_mode": r.raw.get("manifest", {}).get("agent_mode", "unknown")}
                                   for r in findings], ensure_ascii=False) +
                       "\n\nRESOURCE RANKS AND EDIT PERMISSIONS:\n" + json.dumps(resources, ensure_ascii=False) +
                       "\n\nASSESSMENT AND CHANGE HISTORY:\n" + json.dumps(history, ensure_ascii=False) +
                       "\n\nPropose addressed prose changes with reasons and supplied reflection IDs. "
                       "Do not change ranks, release identity, hooks, MCPs, commands, workflows or executable files.")
            executor = Reflector(implementation=self.reflector.implementation, runner=self.reflector.runner,
                                 artifact_dir=self.reflector.artifact_dir or tools._workspace / "reflector",
                                 **self.reflector.config.model_dump())
            if histories and executor.implementation == "codex" and executor.runner is None:
                from thearc.learning.reflection.providers.codex import _run_isolated_sdk

                evidence_tools = EvidenceTools(histories)
                def inspect(prompt, schema, config):
                    return _run_isolated_sdk(prompt, None, config,
                                             instructions="Curate configuration from supplied findings and history.",
                                             scoped_tools=evidence_tools)
                executor = Reflector(implementation="codex", **self.reflector.config.model_dump(),
                                     runner=inspect, artifact_dir=executor.artifact_dir)
                request += "\n\nUse arc_evidence to inspect cited events when needed. Available reflection IDs: "
                request += json.dumps(list(histories))
            response = executor.reflect(request, agent=baseline, output_schema=output_schema,
                                        validate_resources=validate_resources)
            typed_output = response.output
            serialized = (typed_output.model_dump(mode="json", by_alias=True)
                          if isinstance(typed_output, BaseModel) else typed_output)
            engine.actor.update(model_output_sha256=content_hash(serialized), reflection_run_id=response.id)
            # Persist the schema and actual model result before candidate validation/commit.
            with (tools._workspace / "output-schema.json").open("x", encoding="utf-8") as stream:
                json.dump({"schema": schema_snapshot, "output": serialized}, stream)
            report = adapter(typed_output) if adapter is not None else typed_output
            if not isinstance(report, CurationReport):
                raise TypeError("Curation adapter must return CurationReport")
            if validate_resources:
                report.validate_against(baseline)
            for change in report.changes:
                tools.call("arc_change", {
                    "target_json": change.target.model_dump_json(), "operation": change.operation.value,
                    "value_json": json.dumps(change.value), "reason": change.rationale,
                    "reflection_ids": change.reflection_ids,
                })
            return {"status": "completed", "final_response": json.dumps({"summary": report.summary})}

        engine = AgentCurator(run, allow_observations=True,
                              actor={"backend": self.reflector.implementation, "interface": "resources-v1",
                                     "output_schema": schema_snapshot,
                                     "schema_sha256": content_hash(schema_snapshot)})

        def execute(directory):
            return run_curation(baseline, findings, engine, output=Path(directory).resolve(),
                                result_version=result_version)

        if output is None:
            with TemporaryDirectory(prefix="thearc-curation-") as directory:
                epoch = execute(directory)
        else:
            epoch = execute(output)
        # Record schema even for custom reports; existing epoch format stays compatible.
        details = {**epoch, "output_schema": schema_for(output_schema),
                   "model_output": typed_output.model_dump(mode="json")
                   if isinstance(typed_output, BaseModel) else typed_output}
        return CurationResult(json.dumps(epoch["agent"]), tuple(Record(c) for c in epoch["changes"]),
                              epoch["summary"], typed_output, freeze(details))
