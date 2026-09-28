"""Host-enforced, addressed curator capabilities; no arbitrary paths or execution."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event, RLock

from thearc.learning.ace.pipeline import Curation, Reflection
from thearc.learning.curation.assessments import AssessmentLedger, RankBaseline, _content
from thearc.learning.evidence.snapshot import content_hash
from thearc.learning.privacy import redact
from thearc.models import MarkdownDocument, MetaAgent, Operation, ResourceTarget


def resource_targets(agent: MetaAgent) -> list[ResourceTarget]:
    targets = []
    for kind, collection in (("skill", agent.skills), ("hook", agent.hooks),
                             ("mcp", agent.mcps), ("context", agent.context)):
        targets.extend(ResourceTarget(kind=kind, name=name) for name in collection)
    targets.extend(ResourceTarget(kind="skill_file", name=name) for name, _ in agent.iter_skill_files())
    targets.extend(ResourceTarget(kind=item.location[:-1], name=item.path) for item in agent.resources.values())
    return sorted(targets, key=lambda t: (t.kind, t.name))


def _documents(agent):
    for skill in agent.skills.values():
        yield ResourceTarget(kind="skill", name=skill.name), skill.instructions
    for name, text in agent.iter_skill_files(markdown_only=True):
        yield ResourceTarget(kind="skill_file", name=name), text
    for item in agent.context.values():
        yield ResourceTarget(kind="context", name=item.filename), item.content
    for item in agent.resources.values():
        yield ResourceTarget(kind=item.location[:-1], name=item.path), item.content


def _sections(sections):
    for section in sections:
        yield section
        yield from _sections(section.subsections)


def has_ranks(agent: MetaAgent) -> bool:
    if any(skill.ranks is not None or "ranks" in skill.metadata for skill in agent.skills.values()):
        return True
    if any(hook.ranks is not None or "ranks" in hook.extra for hook in agent.hooks.values()):
        return True
    for _, text in _documents(agent):
        document = MarkdownDocument.parse(text)
        if "ranks" in document.frontmatter or any(s.ranks is not None for s in _sections(document.sections)):
            return True
    return False


def seed_rank_history(agent: MetaAgent, ledger: AssessmentLedger) -> AssessmentLedger:
    """Retain even unassessed imported counters before creating a rank-free editor."""
    ledger = ledger.model_copy(deep=True)
    known = {item.target for item in ledger.baselines}
    targets = resource_targets(agent)
    for target, text in _documents(agent):
        targets.extend(target.model_copy(update={"section": section.title})
                       for section in _sections(MarkdownDocument.parse(text).sections))
    for target in targets:
        if target in known or target.kind == "mcp":
            continue
        if target.kind == "skill_file" and not target.name.endswith(".md"):
            continue
        try:
            ranks = agent.rank_for(target)
        except KeyError:
            continue  # Ambiguous sections cannot be addressed; never guess.
        if any(ranks.model_dump().values()):
            ledger.baselines.append(RankBaseline(
                target=target, revision=content_hash(_content(agent.without_ranks(), target)), ranks=ranks,
            ))
            known.add(target)
    return ledger


class CurationTools:
    """A trusted callback runner may use these tools, but is not itself sandboxed."""

    def __init__(self, agent: MetaAgent, reflections: list[Reflection], ledger: AssessmentLedger,
                 history: list[dict], workspace: Path, *, max_calls: int = 100, max_chars: int = 40_000):
        self._agent = agent.without_ranks()
        self._reflections = {item.id: item.model_copy(deep=True) for item in reflections}
        self._ledger = ledger.model_copy(deep=True)
        self._history = history
        self._workspace = workspace
        self.max_calls, self.max_chars = max_calls, max_chars
        self.calls = []
        self.inspections: set[str] = set()
        self.changes: list[Curation] = []
        self._closed = Event()
        self._lock = RLock()
        self.candidate_path = self._agent.to_workspace(workspace / "candidate-000")

    def close(self):
        with self._lock:
            self._closed.set()

    @property
    def definitions(self) -> list[dict]:
        def tool(name, description, fields):
            return {"type": "function", "name": name, "description": description,
                    "inputSchema": {"type": "object", "properties": fields,
                                    "required": list(fields), "additionalProperties": False}}
        string = {"type": "string"}
        return [
            tool("arc_list", "List candidate resource addresses and whether edits are allowed.", {}),
            tool("arc_read", "Read a resource/section as data, with its ranks; page long text using offset.",
                 {"target_json": string, "offset": {"type": "integer", "minimum": 0}}),
            tool("arc_history", "Read a page of assessment/change history for a target.",
                 {"target_json": string, "offset": {"type": "integer", "minimum": 0}}),
            tool("arc_change", "Apply an addressed ADD/EDIT/REMOVE to candidate files. No rank or release edits.",
                 {"target_json": string, "operation": {"type": "string", "enum": ["ADD", "EDIT", "REMOVE"]},
                  "value_json": string, "reason": string,
                  "reflection_ids": {"type": "array", "items": string, "minItems": 1}}),
        ]

    def _editable(self, target):
        # First live policy: prose only. Commands, hooks, MCPs and executable
        # bundled files remain reviewable but cannot be changed by inference.
        return target.kind in {"skill", "context", "rule"} or (
            target.kind == "skill_file" and target.name.endswith(".md")
        )

    def call(self, name: str, arguments: dict):
        with self._lock:
            if self._closed.is_set() or len(self.calls) >= self.max_calls:
                raise ValueError("Curation tools closed or call budget exhausted")
            expected = next((tool for tool in self.definitions if tool["name"] == name), None)
            if expected is None or set(arguments) != set(expected["inputSchema"]["properties"]):
                raise ValueError("Unknown tool or unexpected arguments")
            if len(json.dumps(arguments)) > self.max_chars * 4:
                raise ValueError("Tool arguments exceed limit")
            self.calls.append({"tool": name, "arguments": redact(arguments)})
            if name == "arc_list":
                return [{"target": target.model_dump(), "editable": self._editable(target)}
                        for target in resource_targets(self._agent)]
            target = ResourceTarget.model_validate_json(arguments["target_json"])
            if name == "arc_change":
                return self._change(target, arguments)
            offset = arguments["offset"]
            if type(offset) is not int or offset < 0:
                raise ValueError("Invalid offset")
            if name == "arc_read":
                content = json.dumps(redact(_content(self._agent, target)), ensure_ascii=False)
                if offset >= len(content):
                    raise ValueError("Read offset is outside the resource")
                self.inspections.add("resource")
                return {"content": content[offset:offset + self.max_chars], "total_chars": len(content),
                        "next_offset": offset + self.max_chars if offset + self.max_chars < len(content) else None,
                        "lifetime_ranks": self._ledger.ranks(target).model_dump(),
                        "current_ranks": self._ledger.ranks(
                            target, revision=content_hash(_content(self._agent, target))).model_dump()}
            records = [item.model_dump(mode="json") for item in self._ledger.assessments if item.target == target]
            records += [change for epoch in self._history for change in epoch["changes"]
                        if change["target"] == target.model_dump()
                        or any(edit["target"] == target.model_dump() for edit in change.get("edits", []))]
            # Return one bounded record at a time; do not silently truncate JSON.
            page = redact(records[offset:offset + 1])
            if len(json.dumps(page)) > self.max_chars:
                raise ValueError("History record exceeds tool limit")
            self.inspections.add("history")
            return {"records": page, "total": len(records),
                    "next_offset": offset + 1 if offset + 1 < len(records) else None}

    def _change(self, target, arguments):
        if not self._editable(target):
            raise ValueError("Only prose targets are editable in this policy")
        ids = arguments["reflection_ids"]
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(i, str) or i not in self._reflections for i in ids)
                or not isinstance(arguments["reason"], str) or not arguments["reason"].strip()):
            raise ValueError("Every change requires a reason and known reflection IDs")
        if any(not self._reflections[i].items for i in ids):
            raise ValueError("Changes must cite reflections with evidence-backed items")
        value = json.loads(arguments["value_json"])
        operation = Operation(arguments["operation"])
        if redact(value) != value:
            raise ValueError("Sensitive-looking content cannot be introduced through curation")
        if operation != Operation.ADD:
            original = self._agent.read_target(target)
            if redact(original) != original:
                raise ValueError("Sensitive-looking resources are read-only")
        candidate = self._agent.model_copy(deep=True)
        candidate.apply_change(target, operation, value)
        if has_ranks(candidate):
            raise ValueError("Historical ranks are host-owned, not editable")
        # A whole skill edit must not bypass the prose-only policy for bundled scripts.
        for name in self._agent.skills.keys() | candidate.skills.keys():
            before = self._agent.skills.get(name)
            after = candidate.skills.get(name)
            old = {key: text for key, text in (before.files if before else {}).items() if not key.endswith(".md")}
            new = {key: text for key, text in (after.files if after else {}).items() if not key.endswith(".md")}
            if old != new:
                raise ValueError("Executable/opaque bundled files are read-only")
        # Persist and import every accepted edit through the canonical file format.
        path = candidate.to_workspace(self._workspace / f"candidate-{len(self.changes) + 1:03d}")
        self._agent = MetaAgent.from_workspace(path)
        self.candidate_path = path
        self.changes.append(Curation(
            id=f"edit-{len(self.changes) + 1:03d}", target=target, operation=operation,
            value=value, reflection_ids=ids, rationale=arguments["reason"],
            evidence_event_ids=sorted({
                e.event_id for i in ids for item in self._reflections[i].items for e in item.evidence
            }),
        ))
        return {"status": "applied", "edit_id": self.changes[-1].id}
