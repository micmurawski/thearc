"""Reflection from prompts or history, with typed output or validated tool submission."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from thearc.learning.curation.models import AssessmentReport
from thearc.learning.reflection.runtime import (
    INSTRUCTIONS,
    ExecutionConfig,
    NativeSessionRef,
    ReflectionError,
    _hash,
    resource_catalog,
)
from thearc.learning.sessions.models import Event
from thearc.models import MetaAgent

from .formats import codex_items, provenance_only
from .history import History, Session, SessionView
from .immutable import freeze, thaw
from .schemas import ResourceAssessmentReport, native_output_schema, parse_output, schema_for


@dataclass(frozen=True)
class Reflection:
    id: str
    text: str
    output: Any
    provenance: Any
    execution: Any = field(default_factory=dict)
    history: History | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path):
        """Read a versioned artifact without importing its original schema class."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        schema = schema_for(data.get("schema"))
        manifest = data["provenance"]
        if manifest.get("version") != 1 or manifest.get("schema_sha256") != (
            _hash(schema) if schema is not None else None
        ):
            raise ValueError("Unsupported or inconsistent reflection artifact")
        parsed = parse_output(data["text"], schema, schema)
        saved = data["output"]

        def preserves(original, value):
            if isinstance(original, dict):
                return isinstance(value, dict) and all(
                    key in value and preserves(item, value[key]) for key, item in original.items()
                )
            if isinstance(original, list):
                return (
                    isinstance(value, list)
                    and len(value) == len(original)
                    and all(preserves(a, b) for a, b in zip(original, value, strict=True))
                )
            return original == value

        if not preserves(parsed, saved):
            raise ValueError("Saved output differs from the reflector response")
        if schema is not None:
            parse_output(json.dumps(saved), schema, schema)
        return cls(data["id"], data["text"], saved, freeze(manifest), freeze(data["execution"]))

    @property
    def agent_revision(self):
        return self.provenance.get("config_sha256")

    @property
    def agent_mode(self):
        return self.provenance["agent_mode"]

    def assessments(self, history):
        if not isinstance(self.output, ResourceAssessmentReport):
            raise TypeError("Automatic assessments require ResourceAssessmentReport or an explicit adapter")
        if self.agent_revision is None:
            raise ValueError("Assessments require the evaluated MetaAgent revision")
        items = self.output.assessment_items(history)
        events = history.selected_events if isinstance(history, History) else history
        return AssessmentReport(
            id=self.id,
            summary=self.output.summary,
            items=items,
            limitations=self.output.limitations,
            session_ids=list(dict.fromkeys(e.session_id for e in events)),
            evidence_event_ids=list(dict.fromkeys(e.event_id for item in items for e in item.evidence)),
            raw={"manifest": thaw(self.provenance)},
        )


class Reflector:
    """An evaluator. `agent=` on each call supplies the evaluated configuration.

    A supplied runner is trusted application code with signature (prompt, schema,
    config). Native history and activation use the verified built-in adapter only.
    With ReflectionTools, reflect/handoff return the accepted list of Reflections.
    Without tools they return a single Reflection containing the final response.
    tool_transport="mcp" owns a temporary MCP endpoint; "host" uses scoped host tools.
    """

    def __init__(self, *, implementation, model, runner=None, artifact_dir=None, tool_transport="host", **settings):
        if implementation not in {"codex", "claude", "antigravity", "custom"}:
            raise ValueError(f"Unknown implementation: {implementation}")
        if tool_transport not in {"host", "mcp"}:
            raise ValueError("tool_transport must be host or mcp")
        if tool_transport == "mcp" and (implementation != "codex" or runner is not None):
            raise ReflectionError("capability", "Managed MCP reflection requires the built-in Codex adapter")
        self.implementation = implementation
        self.tool_transport = tool_transport
        self.config = ExecutionConfig(model=model, **settings)
        self.runner = runner
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else None

    def prepare(
        self,
        prompt,
        *,
        agent=None,
        agent_mode="reference",
        output_schema=None,
        validate_resources=False,
        tools=None,
        include_catalog=True,
    ):
        """``include_catalog=False`` is for callers whose prompt already presents the agent."""
        if self.tool_transport == "mcp" and tools is None:
            raise ValueError("MCP reflection requires ReflectionTools")
        if tools is not None:
            from .reflection_tools import ReflectionTools

            if not isinstance(tools, ReflectionTools):
                raise TypeError("tools must be ReflectionTools")
            if output_schema is not None or agent_mode != "reference":
                raise ValueError("ReflectionTools owns the reflection schema and evaluated reference agent")
            if agent is not None:
                if not isinstance(agent, MetaAgent):
                    raise TypeError("agent must be a MetaAgent")
                if agent.without_ranks() != tools.agent:
                    raise ValueError("ReflectionTools was prepared for a different MetaAgent")
            if tools.accepted:
                raise ValueError("Use a new ReflectionTools instance for each run")
            if self.implementation != "codex" or self.runner is not None:
                raise ReflectionError(
                    "capability",
                    "In-process reflection tools require built-in Codex; "
                    "use the reflection MCP server with other harnesses",
                )
            agent = tools.agent
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A nonempty prompt is required")
        if agent_mode not in {"reference", "active"}:
            raise ValueError("agent_mode must be reference or active")
        if (validate_resources or agent_mode == "active") and agent is None:
            raise ValueError("Resource validation and activation require a MetaAgent")
        if agent is not None and not isinstance(agent, MetaAgent):
            raise TypeError("agent must be a MetaAgent")
        snapshot = agent.without_ranks() if agent is not None else None
        schema = tools.schema if tools is not None else schema_for(output_schema)
        if validate_resources and schema is None:
            raise ValueError("Resource validation requires an output schema")
        active = ""
        if agent_mode == "active":
            if self.implementation != "codex" or self.runner is not None:
                raise ReflectionError("capability", "Activation is supported only by the built-in Codex adapter")
            if (
                snapshot.skills
                or snapshot.hooks
                or snapshot.mcps
                or any(r.location != "rules" for r in snapshot.resources.values())
            ):
                raise ReflectionError(
                    "capability",
                    "Codex activation currently supports context and rules only; "
                    "skills, hooks, MCPs, commands and workflows require additional adapters",
                )
            active = "\n\n".join(
                [doc.content for doc in snapshot.context.values()] + [r.content for r in snapshot.resources.values()]
            )
        effective = prompt
        if tools is not None:
            effective += "\n\n" + tools.instructions
        elif snapshot is not None and include_catalog:
            effective += "\n\nAGENT RESOURCE CATALOG (reference data):\n" + json.dumps(
                resource_catalog(snapshot),
                ensure_ascii=False,
            )
        if schema is not None and tools is None:
            effective += "\n\nReturn JSON matching this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
        if len(effective) + len(active) > self.config.max_input_chars:
            raise ReflectionError("input_too_large", "Request exceeds input limit; select a smaller input explicitly")
        return {
            "prompt": effective,
            "schema": schema,
            "active_instructions": active,
            **({"tools": tools} if tools is not None else {}),
            "agent": snapshot,
            "manifest": {
                "version": 1,
                "implementation": self.implementation,
                "settings": self.config.model_dump(),
                "agent_mode": agent_mode,
                "activated_resources": [entry["target"] for entry in resource_catalog(snapshot)]
                if agent_mode == "active"
                else [],
                "config_sha256": _hash(snapshot.model_dump(mode="json")) if snapshot is not None else None,
                "configuration_provenance": "supplied snapshot; historical installation/loading is unverified",
                "schema_sha256": _hash(schema) if schema is not None else None,
                "prompt_sha256": _hash(effective),
                **({"tool_transport": self.tool_transport} if tools is not None else {}),
            },
        }

    def reflect(
        self,
        prompt,
        *,
        agent=None,
        agent_mode="reference",
        output_schema=None,
        validate_resources=False,
        tools=None,
        include_catalog=True,
    ):
        prepared = self.prepare(
            prompt,
            agent=agent,
            agent_mode=agent_mode,
            output_schema=output_schema,
            validate_resources=validate_resources,
            tools=tools,
            include_catalog=include_catalog,
        )
        return self._execute(prepared, output_schema, validate_resources)

    def handoff(
        self,
        prompt,
        history,
        *,
        agent=None,
        agent_mode="reference",
        output_schema=None,
        validate_resources=False,
        tools=None,
    ):
        if not isinstance(history, History):
            raise TypeError("handoff requires a Session, Trajectory or SessionView")
        prepared = self.prepare(
            prompt,
            agent=agent,
            agent_mode=agent_mode,
            output_schema=output_schema,
            validate_resources=validate_resources,
            tools=tools,
        )
        if self.implementation != "codex" or self.runner is not None:
            raise ReflectionError("capability", "Historical handoff requires the built-in Codex adapter")
        session = history if isinstance(history, Session) else history.session
        selected = history.selected_events
        if not selected:
            raise ValueError("Cannot hand off empty history")
        if tools is not None and not tools.matches_history(history):
            raise ValueError("ReflectionTools evidence must match the handoff history")
        streams = {(e.run_id, e.reference.path, e.reference.generation) for e in selected}
        if len(streams) != 1:
            raise ReflectionError("capability", "Handoff across multiple streams requires a known causal order")
        prepared["manifest"]["history"] = {
            "artifact_id": session.artifact_id,
            "session_id": session.id,
            "event_ids": [e.id for e in selected],
        }
        prepared["history_resource"] = history
        # A prefix requires an exact recorded native turn; never fork 'latest'.
        turn = history.native_turn_id if isinstance(history, SessionView) else None
        if isinstance(history, Session):
            trajectories = history.split_into_trajectories()
            if trajectories and trajectories[-1].selected_events[-1].id == selected[-1].id:
                turn = trajectories[-1].native_turn_id
        native = (
            isinstance(history, (Session, SessionView))
            and session._archive is not None
            and session.metadata.harness == "codex"
            and turn
        )
        if native:
            if tools is not None and self.tool_transport == "host":
                raise ReflectionError(
                    "capability",
                    "The pinned Codex fork API cannot register host tools. "
                    "Hand off a Trajectory for explicit history import, or attach the MCP server "
                    "with tool_transport='mcp'.",
                )
            if any(e.parent_run_id for e in selected):
                raise ReflectionError("capability", "Native prefix handoff requires one unambiguous main stream")
            # Verify source bytes still contain the captured records before using a mutable native locator.
            for record in selected:
                event = Event.model_validate(record.model_dump())
                with event.reference.path.open("rb") as stream:
                    stream.seek(event.reference.byte_offset)
                    raw = json.loads(stream.read(event.reference.byte_length))
                if raw != event.raw:
                    raise ReflectionError("source_changed", "Native source differs from the captured artifact")
            prepared["fork_from"] = NativeSessionRef(
                runtime="codex", session_id=session.metadata.native_id, through_turn_id=turn
            )
            prepared["manifest"]["mode"] = "native_fork"
        else:
            prepared["history_items"] = codex_items(selected)
            if not prepared["history_items"]:
                raise ReflectionError("capability", "Selection contains no model conversation items to import")
            calls = {
                item["call_id"]
                for item in prepared["history_items"]
                if item["type"] in {"function_call", "custom_tool_call"}
            }
            outputs = {
                item["call_id"]
                for item in prepared["history_items"]
                if item["type"] in {"function_call_output", "custom_tool_call_output"}
            }
            if calls - outputs:
                raise ReflectionError(
                    "capability",
                    "History import requires completed tool call/result pairs; "
                    "use a larger explicit selection or an evidence prompt",
                )
            prepared["manifest"]["mode"] = "import"
            prepared["manifest"]["conversion"] = {
                "model_event_ids": [e.id for e in selected if not provenance_only(e.model_dump())],
                "lifecycle_event_ids": [e.id for e in selected if provenance_only(e.model_dump())],
                "lifecycle_storage": "captured artifact and history.json; not model-visible conversation items",
            }
        return self._execute(prepared, output_schema, validate_resources)

    def _run(self, prepared, receipt):
        if self.runner is not None:
            return self.runner(prepared["prompt"], prepared["schema"], self.config)
        if self.implementation == "codex":
            from contextlib import nullcontext

            from thearc.learning.reflection.providers.codex import _run_isolated_sdk

            from .reflection_mcp import reflection_mcp_transport

            transport = (
                reflection_mcp_transport(prepared["tools"]) if self.tool_transport == "mcp" else nullcontext(None)
            )
            with transport as servers:
                return _run_isolated_sdk(
                    prepared["prompt"],
                    None if "tools" in prepared else native_output_schema(prepared["schema"]),
                    self.config,
                    instructions=INSTRUCTIONS,
                    scoped_tools=prepared.get("tools") if self.tool_transport == "host" else None,
                    fork_from=prepared.get("fork_from"),
                    on_fork=lambda value: receipt(value.model_dump(mode="json")),
                    history_items=prepared.get("history_items"),
                    on_import=receipt,
                    active_instructions=prepared["active_instructions"],
                    **({"mcp_servers": servers} if servers is not None else {}),
                )
        if self.implementation in {"claude", "antigravity"}:
            import importlib

            name = self.implementation
            module = importlib.import_module(f"thearc.learning.reflection.providers.{name}")
            config_name = "ClaudeRuntimeConfig" if name == "claude" else "AntigravityRuntimeConfig"
            config_class = getattr(module, config_name)
            runner = getattr(module, f"_run_{name}")
            schema = prepared["schema"]
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ReflectionError("capability", f"{name} adapter requires an object output schema")
            return runner(
                prepared["prompt"], schema, config_class(**self.config.model_dump()), instructions=INSTRUCTIONS
            )
        raise ReflectionError("missing_runtime", "A custom implementation requires a runner")

    def _execute(self, prepared, spec, validate_resources):
        identifier = f"reflection-{uuid4().hex}"
        directory = self.artifact_dir / identifier if self.artifact_dir is not None else None
        manifest = prepared["manifest"]
        receipt_value = None

        def save(name, data):
            if directory is not None:
                with (directory / name).open("x", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False, indent=2)

        def receipt(value):
            nonlocal receipt_value
            if receipt_value is not None:
                raise ReflectionError("invalid_receipt", "Runtime reported more than one destination")
            receipt_value = value
            save("destination.json", value)

        if directory is not None:
            directory.mkdir(parents=True, exist_ok=False)
        save("request.json", {"prompt": prepared["prompt"], "schema": prepared["schema"], "manifest": manifest})
        if prepared.get("history_resource") is not None:
            save("history.json", [e.model_dump() for e in prepared["history_resource"].selected_events])
        if "tools" in prepared:
            save("resources.json", prepared["tools"].resources.catalog())
            if prepared["tools"].files is not None and directory is not None:
                # The exact lines the reflector read, so cited path#Lx-Ly references stay checkable.
                prepared["tools"].files.write(directory / "agent-files")
        try:
            response = self._run(prepared, receipt)
            save("response.json", response)
            if not isinstance(response, dict) or response.get("status") != "completed":
                raise ReflectionError("incomplete", "Reflector did not complete")
            if "tools" in prepared:
                if "mode" in manifest and (
                    receipt_value is None or response.get("thread_id") != receipt_value["session_id"]
                ):
                    raise ReflectionError("invalid_receipt", "Reflection does not belong to the prepared destination")
                tools = prepared["tools"]
                results = []
                execution = {key: value for key, value in response.items() if key != "final_response"}
                if receipt_value is not None:
                    execution["destination"] = receipt_value
                for reflection in tools.reflections:
                    provenance = {**manifest, **thaw(reflection.provenance)}
                    result = Reflection(
                        reflection.id,
                        reflection.text,
                        reflection.output,
                        freeze(provenance),
                        freeze(execution),
                        prepared.get("history_resource") or reflection.history,
                    )
                    if directory is not None:
                        (directory / result.id).mkdir()
                    save(
                        f"{result.id}/reflection.json",
                        {
                            "id": result.id,
                            "text": result.text,
                            "schema": prepared["schema"],
                            "output": json.loads(result.text),
                            "provenance": provenance,
                            "execution": execution,
                        },
                    )
                    results.append(result)
                save(
                    "submission.json",
                    {
                        "accepted": True,
                        "reflection_ids": [r.id for r in results],
                        "resource_ids": tools.resources.mapping,
                        "assessments": [r.model_dump(mode="json") for r in tools.assessments],
                    },
                )
                return results
            text = response.get("final_response")
            if not isinstance(text, str) or not text.strip():
                raise ReflectionError("invalid_output", "Reflector returned no final response")
            if "mode" in manifest and (
                receipt_value is None or response.get("thread_id") != receipt_value["session_id"]
            ):
                raise ReflectionError("invalid_receipt", "Reflection does not belong to the prepared destination")
            output = parse_output(
                text, spec, prepared["schema"], agent=prepared["agent"], validate_resources=validate_resources
            )
            execution = {key: value for key, value in response.items() if key != "final_response"}
            if receipt_value is not None:
                execution["destination"] = receipt_value
            result = Reflection(
                identifier, text, output, freeze(manifest), freeze(execution), prepared.get("history_resource")
            )
            serialized = output.model_dump(mode="json", by_alias=True) if isinstance(output, BaseModel) else output
            save(
                "reflection.json",
                {
                    "id": identifier,
                    "text": text,
                    "schema": prepared["schema"],
                    "output": serialized,
                    "provenance": manifest,
                    "execution": execution,
                },
            )
            return result
        except BaseException as exc:
            save("failure.json", {"error_type": type(exc).__name__, "code": getattr(exc, "code", None)})
            raise
        finally:
            if "tools" in prepared:
                prepared["tools"].close()
