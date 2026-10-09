"""Harness-independent reflection submission and bounded resource access."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel

from thearc.learning.curation.models import AssessmentReport, ReflectionItem
from thearc.learning.evidence.snapshot import write_json_exclusive
from thearc.learning.reflection.runtime import ReflectionError, _hash
from thearc.models import MetaAgent

from .formats import immutable_events
from .history import History
from .immutable import freeze, thaw
from .reflection import Reflection
from .resource_view import ResourceView
from .schemas import (
    ResourceAssessmentReport,
    ResourceReference,
    ResourceTag,
    TaggedReflection,
    _validator,
    parse_output,
    schema_for,
)


def _list_schema(item_schema):
    """Keep local references, including recursive ones, valid inside the tool envelope."""

    def rebase(node):
        if isinstance(node, list):
            return [rebase(value) for value in node]
        if not isinstance(node, dict):
            return node
        result = {}
        for key, value in node.items():
            if key in {"$id", "$anchor", "$dynamicAnchor"}:
                raise ValueError("Submission schemas require local JSON pointers, without schema IDs or anchors")
            if key in {"$ref", "$dynamicRef"}:
                if value != "#" and not value.startswith("#/"):
                    raise ValueError("Submission schemas require local JSON pointer references")
                result[key] = "#/$defs/Reflection" + value[1:]
            else:
                result[key] = rebase(value)
        return result

    return {
        "type": "object",
        "properties": {
            "reflections": {"type": "array", "maxItems": 100, "items": {"$ref": "#/$defs/Reflection"}},
        },
        "required": ["reflections"],
        "additionalProperties": False,
        "$defs": {"Reflection": rebase(item_schema)},
    }


class ReflectionTools:
    """One submission batch tied to a frozen agent and explicitly supplied evidence.

    The same definitions/call interface works with host tools and MCP. Validation
    failures are returned to the model; no model call or rank mutation happens here.
    """

    _finish = (
        "Finish by calling submit_reflections with the complete list (an empty list is valid when there are "
        "no findings). Correct any returned validation errors and resubmit. Only an accepted submission "
        "is a result. After acceptance, stop; do not submit another batch."
    )
    guidance = (
        "Inspect the supplied history/evidence. The evaluated agent's resources are listed below with their IDs; "
        "read their content with read_resource and use the IDs in bullet_tags. list_resources searches the same "
        "IDs. list_evidence locates event IDs in the supplied scope. " + _finish
    )
    file_guidance = (
        "Inspect the supplied history/evidence. The evaluated agent is a set of files, listed below with the "
        "line ranges of their sections. Read them with read_file and cite agent lines in resource_refs as "
        "path#Lstart-Lend (or path#Lline). Each citation is credited to the innermost section containing it, "
        "so cite lines inside one section or a whole section; a range that ends inside a different section is "
        "rejected. The supplied history is the evidence for every finding. " + _finish
    )
    max_index_chars = 40_000

    @property
    def instructions(self):
        if self.files is not None:
            return self.file_guidance + "\n\n" + self.file_index()
        return self.guidance + "\n\n" + self.resource_index()

    def file_index(self):
        """Every agent file with its section line ranges, so citations need no guessing."""
        blocks = []
        for path in self.files.files:
            outline = [span for span in self.files.outline(path) if span.target.section or span.path.endswith(".json")]
            rows = [
                f"  L{span.start}-L{span.end} "
                + (
                    f"{span.target.section} [SectionId: {span.target.id}]"
                    if span.target.section
                    else f"{span.target.kind}: {span.target.name}"
                )
                for span in outline
            ]
            blocks.append((f"{path} ({self.files.line_count(path)} lines)", rows))
        text = "\n".join("\n".join([head, *rows]) for head, rows in blocks)
        if len(text) > self.max_index_chars:
            text = "\n".join(head for head, _ in blocks) + "\n(Section ranges omitted for size; read the files.)"
        return f"AGENT FILES ({len(blocks)}; path (lines), then section line ranges):\n" + text

    def resource_index(self):
        """Every addressable resource ID, so tagging never depends on what a search happened to find."""
        rows = [(key, target) for key, target in self.resources.mapping.items()]
        lines = [
            f"{key} | {t['kind']} | {t['name']}" + (f" | {t['section']}" if t["section"] else "") for key, t in rows
        ]
        text = "\n".join(lines)
        if len(text) > self.max_index_chars:
            # Very large agents: whole resources stay listed; sections remain reachable via list_resources.
            whole = [line for line, (_, t) in zip(lines, rows, strict=True) if not t["section"]]
            text = "\n".join(whole) + (
                f"\n({len(lines) - len(whole)} section IDs omitted for size; find them with list_resources.)"
            )
        return (
            f"AGENT RESOURCES ({len(rows)} IDs; id | kind | name | section):\n" + text
            if rows
            else "AGENT RESOURCES: none. Leave bullet_tags empty."
        )

    def __init__(
        self,
        *,
        agent: MetaAgent,
        evidence,
        reflection_schema=TaggedReflection,
        ledger=None,
        output=None,
        max_submissions=3,
        max_calls=100,
        page_chars=8000,
    ):
        for name, value in (("max_submissions", max_submissions), ("max_calls", max_calls), ("page_chars", page_chars)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(agent, MetaAgent):
            raise TypeError("agent must be a MetaAgent")
        self._agent = agent.without_ranks()
        self.resources = ResourceView(agent, ledger=ledger)
        self._spec = reflection_schema
        self._schema = schema_for(reflection_schema)
        if not isinstance(self._schema, dict) or self._schema.get("type") != "object":
            raise ValueError("reflection_schema must describe one reflection object")
        self._submission_schema = _list_schema(self._schema)
        # Schemas with resource_refs cite agent files by line; others use resource IDs.
        properties = self._schema.get("properties", {})
        self.files = agent.files() if "resource_refs" in properties else None
        # Without event_ids, each rating rests on the whole supplied history.
        self.cites_events = "event_ids" in properties
        self.history = evidence if isinstance(evidence, History) else None
        self._events = self.history.selected_events if self.history is not None else immutable_events(evidence)
        self._event_sessions = {event.id: event.session_id for event in self._events}
        if len(self._event_sessions) != len(self._events):
            raise ValueError("Evidence event IDs must be unique")
        self._evidence_hash = _hash([event.model_dump() for event in self._events])
        self.output = Path(output) if output is not None else None
        if self.output is not None and self.output.exists():
            raise FileExistsError(self.output)
        self.max_submissions, self.max_calls, self.page_chars = max_submissions, max_calls, page_chars
        self._attempts = 0
        self._calls = 0
        self._accepted = None
        self._reflections = []
        self._assessments = []
        self._closed = False
        self._lock = RLock()
        self._batch_id = uuid4().hex

    @property
    def agent(self):
        return self._agent.model_copy(deep=True)

    @property
    def schema(self):
        return json.loads(json.dumps(self._schema))

    def matches_history(self, history):
        return self._evidence_hash == _hash([event.model_dump() for event in history.selected_events])

    @property
    def accepted(self):
        return self._accepted is not None

    @property
    def reflections(self):
        if not self.accepted:
            raise ReflectionError("missing_submission", "No reflection list has been accepted")
        # Models are mutable; callers must not change an accepted batch in place.
        from copy import deepcopy

        return [
            Reflection(r.id, r.text, deepcopy(r.output), r.provenance, r.execution, r.history)
            for r in self._reflections
        ]

    @property
    def assessments(self):
        if not self.accepted:
            raise ReflectionError("missing_submission", "No reflection list has been accepted")
        return [item.model_copy(deep=True) for item in self._assessments]

    def close(self):
        with self._lock:
            self._closed = True

    @property
    def definitions(self):
        def tool(name, description, fields):
            return {
                "type": "function",
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": fields,
                    "required": list(fields),
                    "additionalProperties": False,
                },
            }

        paging = {
            "query": {"type": "string", "maxLength": 500},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }
        if self.files is not None:
            access = [
                tool("list_files", "List the agent's files with their line counts.", {}),
                tool(
                    "read_file",
                    "Read numbered lines of one agent file. Continue from next_line.",
                    {
                        "path": {"type": "string", "maxLength": 500},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                ),
            ]
        else:
            access = [
                tool(
                    "list_resources",
                    "Search resource names and section titles; empty query lists all. Paged IDs only.",
                    {**paging, "include_ranks": {"type": "boolean"}},
                ),
                tool(
                    "read_resource",
                    "Read one resource/section page by ID. Continue using next_offset.",
                    {
                        "id": {"type": "string", "maxLength": 100},
                        "offset": {"type": "integer", "minimum": 0},
                        "include_ranks": {"type": "boolean"},
                    },
                ),
            ]
        evidence = (
            [
                tool(
                    "list_evidence",
                    "Locate event IDs by literal text search in the supplied evidence. Paged previews.",
                    paging,
                )
            ]
            if self.cites_events
            else []
        )
        return [
            *access,
            *evidence,
            {
                "type": "function",
                "name": "submit_reflections",
                "description": "Validate and accept the complete reflection list. Fix errors and retry before finishing.",
                "inputSchema": json.loads(json.dumps(self._submission_schema)),
            },
        ]

    @staticmethod
    def _error(path, code, message):
        return {"path": path, "code": code, "message": message[:500]}

    def _rejected(self, errors):
        return {
            "accepted": False,
            "errors": errors[:10],
            "attempts_remaining": max(0, self.max_submissions - self._attempts),
        }

    def call(self, name, arguments):
        with self._lock:
            if self._closed or self._calls >= self.max_calls:
                return self._rejected([self._error("", "closed", "Tools closed or call budget exhausted")])
            self._calls += 1
            definition = next((item for item in self.definitions if item["name"] == name), None)
            if definition is None:
                return self._rejected([self._error("", "unknown_tool", "Unknown tool")])
            if name == "submit_reflections":
                if self.accepted:
                    if arguments == self._accepted:
                        return self._receipt()  # An identical transport retry has no side effects.
                    return self._rejected([self._error("", "already_submitted", "This batch is already accepted")])
                if self._attempts >= self.max_submissions:
                    return self._rejected([self._error("", "attempt_limit", "Submission attempt limit reached")])
                self._attempts += 1
            if len(json.dumps(arguments, ensure_ascii=False)) > 256_000:
                return self._rejected([self._error("", "input_too_large", "Tool arguments exceed 256000 characters")])
            errors = []
            for error in _validator(definition["inputSchema"]).iter_errors(arguments):
                path = ".".join(str(part) for part in error.absolute_path)
                errors.append(self._error(path, "schema", error.message))
                if len(errors) == 10:
                    break
            if errors:
                return self._rejected(errors)
            try:
                if name == "submit_reflections":
                    return self._submit(arguments)
                if name == "list_files":
                    result = {
                        "files": [{"path": path, "lines": self.files.line_count(path)} for path in self.files.files]
                    }
                elif name == "read_file":
                    result = self._read_file(**arguments)
                elif name == "list_resources":
                    result = self.resources.list(**arguments)
                elif name == "read_resource":
                    result = self.resources.read(
                        arguments["id"],
                        offset=arguments["offset"],
                        include_ranks=arguments["include_ranks"],
                        max_chars=self.page_chars,
                    )
                else:
                    matches = [
                        event for event in self._events if arguments["query"].casefold() in event.text.casefold()
                    ]
                    start, end = arguments["offset"], arguments["offset"] + arguments["limit"]
                    result = {
                        "events": [
                            {"event_id": e.id, "role": e.role, "kind": e.kind, "preview": e.text[:160]}
                            for e in matches[start:end]
                        ],
                        "total": len(matches),
                        "next_offset": end if end < len(matches) else None,
                    }
                if len(json.dumps(result, ensure_ascii=False)) > self.page_chars * 4:
                    raise ValueError("Page exceeds response limit; request fewer entries")
                return result
            except (ValueError, KeyError) as exc:
                return self._rejected([self._error("", "invalid_reference", str(exc))])

    def _read_file(self, path, start_line, end_line):
        total = self.files.line_count(path)
        if start_line > max(total, 1) or end_line < start_line:
            raise ValueError(f"{path} has {total} lines; request a range within L1-L{total}")
        end, content = min(end_line, total), ""
        while end >= start_line:  # Shrink the page until it fits the response budget.
            content = self.files.read(path, start_line, end)
            if len(content) <= self.page_chars or end == start_line:
                break
            end = start_line + max(0, (end - start_line) // 2)
        return {
            "path": path,
            "start_line": start_line,
            "end_line": end,
            "total_lines": total,
            "content": content[: self.page_chars],
            "next_line": end + 1 if end < min(end_line, total) else None,
        }

    def _submit(self, arguments):
        outputs, items_by_reflection, errors = [], [], []
        for index, data in enumerate(arguments["reflections"]):
            path = f"reflections[{index}]"
            try:
                output = parse_output(
                    json.dumps(data), self._spec, self._schema, agent=self._agent, validate_resources=True
                )
                canonical = output.model_dump(mode="json", by_alias=True) if isinstance(output, BaseModel) else output
                citations = canonical.get("event_ids", [])
                if (
                    not isinstance(citations, list)
                    or any(not isinstance(eid, str) for eid in citations)
                    or len(set(citations)) != len(citations)
                ):
                    errors.append(self._error(path + ".event_ids", "invalid_evidence", "Use unique event IDs"))
                    continue
                unknown = [eid for eid in citations if eid not in self._event_sessions]
                if unknown:
                    errors.append(
                        self._error(
                            path + ".event_ids",
                            "unknown_event",
                            f"Event outside supplied evidence: {unknown[0]}. Use list_evidence.",
                        )
                    )
                    continue
                if not self.cites_events:
                    citations = list(self._event_sessions)
                ratings = []  # (error path, target, rating, cited lines)
                for field in ("bullet_tags", "resource_refs"):
                    if not isinstance(canonical.get(field, []), list):
                        errors.append(self._error(f"{path}.{field}", "invalid_tags", "Resource tags must be a list"))
                for tag_index, raw_tag in enumerate(canonical.get("bullet_tags", []) or []):
                    tag = ResourceTag.model_validate(raw_tag)
                    tag_path = f"{path}.bullet_tags[{tag_index}].id"
                    try:
                        ratings.append((tag_path, self.resources.resolve(tag.id), tag.tag, None))
                    except ValueError as exc:
                        errors.append(self._error(tag_path, "unknown_resource", str(exc)))
                for ref_index, raw_ref in enumerate(canonical.get("resource_refs", []) or []):
                    ref = ResourceReference.model_validate(raw_ref)
                    ref_path = f"{path}.resource_refs[{ref_index}].ref"
                    if self.files is None:
                        errors.append(self._error(ref_path, "unknown_resource", "This batch rates resource IDs"))
                        continue
                    try:
                        ratings.append((ref_path, self.files.resolve(ref.ref), ref.tag, ref.ref))
                    except ValueError as exc:
                        errors.append(self._error(ref_path, "invalid_reference", str(exc)))
                items, seen = [], set()
                for tag_path, target, rating, reference in ratings:
                    if target in seen:
                        errors.append(
                            self._error(
                                tag_path,
                                "duplicate_resource",
                                "Rate each resource once per reflection"
                                + (
                                    f"; {reference} is credited to {target.section or target.name}" if reference else ""
                                ),
                            )
                        )
                        continue
                    seen.add(target)
                    if not citations:
                        errors.append(
                            self._error(path + ".event_ids", "missing_evidence", "Resource tags require evidence")
                        )
                        continue
                    reason = canonical.get("reasoning")
                    if not isinstance(reason, str) or not reason.strip():
                        errors.append(
                            self._error(path + ".reasoning", "missing_reason", "Resource tags require a reason")
                        )
                        continue
                    items.append(
                        ReflectionItem(
                            target=target,
                            rating=rating,
                            reason=reason,
                            limitations=[],
                            reference=reference,
                            evidence=[{"event_id": eid, "session_id": self._event_sessions[eid]} for eid in citations],
                        )
                    )
                if isinstance(output, ResourceAssessmentReport):
                    items = output.assessment_items(self._events)
                outputs.append(output)
                items_by_reflection.append(items)
            except ValueError as exc:
                errors.append(self._error(path, "validation", str(exc)))
            if len(errors) >= 10:
                break
        if errors:
            return self._rejected(errors)
        manifest = {
            "version": 1,
            "agent_mode": "reference",
            "config_sha256": self.resources.revision,
            "schema_sha256": _hash(self._schema),
            "submission": "tool",
            "evidence_sha256": self._evidence_hash,
            "configuration_provenance": "supplied snapshot; historical installation/loading is unverified",
        }
        reflections, assessments = [], []
        for i, (output, items) in enumerate(zip(outputs, items_by_reflection, strict=True)):
            data = output.model_dump(mode="json", by_alias=True) if isinstance(output, BaseModel) else output
            identifier = f"reflection-{self._batch_id}-{i}"
            provenance = {
                **manifest,
                "resource_ids": {
                    tag["id"]: self.resources.resolve(tag["id"]).model_dump(mode="json")
                    for tag in data.get("bullet_tags", [])
                },
            }
            if self.files is not None:
                provenance["resource_refs"] = {
                    ref["ref"]: {**target.model_dump(mode="json"), "id": target.id}
                    for ref in data.get("resource_refs", [])
                    for target in [self.files.resolve(ref["ref"])]
                }
            reflections.append(
                Reflection(
                    identifier,
                    json.dumps(data, ensure_ascii=False),
                    output,
                    freeze(provenance),
                    freeze({"submission_batch": self._batch_id}),
                    self.history,
                )
            )
            summary = data.get("key_insight") or data.get("summary")
            assessments.append(
                AssessmentReport(
                    id=identifier,
                    summary=summary if isinstance(summary, str) else json.dumps(data, ensure_ascii=False),
                    items=items,
                    session_ids=list(dict.fromkeys(e.session_id for item in items for e in item.evidence)),
                    evidence_event_ids=list(dict.fromkeys(e.event_id for item in items for e in item.evidence)),
                    raw={"manifest": manifest},
                )
            )
        if self.output is not None:
            bundle = {
                "version": 1,
                "accepted": True,
                "schema": self._schema,
                "resource_ids": self.resources.mapping,
                "reflections": [
                    {
                        "id": r.id,
                        "text": r.text,
                        "schema": self._schema,
                        "output": json.loads(r.text),
                        "provenance": thaw(r.provenance),
                        "execution": thaw(r.execution),
                    }
                    for r in reflections
                ],
                "assessments": [item.model_dump(mode="json") for item in assessments],
            }
            self.output.parent.mkdir(parents=True, exist_ok=True)
            write_json_exclusive(self.output, bundle)
        self._reflections, self._assessments = reflections, assessments
        self._accepted = json.loads(json.dumps(arguments))
        return self._receipt()

    def _receipt(self):
        receipt = {
            "accepted": True,
            "reflection_ids": [item.id for item in self._reflections],
            "count": len(self._reflections),
        }
        if self.files is not None:
            # Where each cited range landed: the section (or whole resource) whose rank it changes.
            receipt["ranked"] = [
                {
                    "reflection_id": report.id,
                    "ref": item.reference,
                    "tag": item.rating.value,
                    **(
                        {"section_id": item.target.id, "section": item.target.section}
                        if item.target.section
                        else {"resource": f"{item.target.kind}: {item.target.name}"}
                    ),
                }
                for report in self._assessments
                for item in report.items
            ]
        return receipt
