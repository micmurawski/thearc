"""Scoped, audited retrieval over frozen evidence. No filesystem or SQL tools."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from threading import Event as CancellationEvent

from thearc.learning.evidence.exports import event_groups
from thearc.learning.evidence.snapshot import EvidenceSnapshot, canonical_json, content_hash
from thearc.learning.privacy import hide_historical_ranks, redact
from thearc.learning.runtime.journal import RunJournal
from thearc.models.agent import MetaAgent, ResourceTarget


class EvidenceToolError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RetrievalLimits:
    max_calls: int = 40
    max_response_chars: int = 12_000
    max_total_chars: int = 200_000
    timeout_seconds: float = 180

    def __post_init__(self):
        if min(self.max_calls, self.max_response_chars, self.max_total_chars, self.timeout_seconds) <= 0:
            raise ValueError("Retrieval limits must be positive")


_STRING = {"type": "string"}
_OFFSET = {"type": "integer", "minimum": 0}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
_CURSOR = {"type": ["string", "null"]}
_PAGE = {"cursor": _CURSOR, "limit": _LIMIT}
_READ = {"offset": _OFFSET, "max_chars": {"type": "integer", "minimum": 1, "maximum": 8000}}
_ID = {"session_id": _STRING, "event_id": _STRING}
_SPEC = {
    "list_sessions": (_PAGE, [], "List the selected sessions; no event content is delivered."),
    "search_events": ({**_PAGE, "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                       "session_ids": {"type": "array", "items": _STRING},
                       "kinds": {"type": "array", "items": _STRING}}, ["query"],
                      "Case-insensitive literal search; returns snippets, not full event reads."),
    "read_event": ({**_ID, **_READ}, list(_ID), "Read paged canonical event JSON, with exact character ranges."),
    "read_context": ({**_ID, **_PAGE, "before": {"type": "integer", "minimum": 0, "maximum": 20},
                      "after": {"type": "integer", "minimum": 0, "maximum": 20}}, list(_ID),
                     "Find neighboring whole call groups. Read the returned IDs using read_event for content."),
    "list_resources": (_PAGE, [], "List available configuration targets; read_resource retrieves content."),
    "read_resource": ({"kind": _STRING, "name": _STRING, "section": {"type": ["string", "null"]}, **_READ},
                      ["kind", "name"], "Read rank-free configuration JSON or a specific Markdown section."),
}


def _validate(value, schema):
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    matches = {"string": isinstance(value, str), "integer": type(value) is int,
               "null": value is None, "array": isinstance(value, list)}
    if not any(matches.get(kind, False) for kind in kinds):
        raise EvidenceToolError("invalid_arguments", "Wrong tool argument type")
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 10000):
        raise EvidenceToolError("invalid_arguments", "String length outside bounds")
    if type(value) is int and not schema.get("minimum", 0) <= value <= schema.get("maximum", 10**9):
        raise EvidenceToolError("invalid_arguments", "Integer outside bounds")
    if isinstance(value, list):
        if len(value) > 1000:
            raise EvidenceToolError("invalid_arguments", "Too many filter values")
        for item in value:
            _validate(item, schema["items"])


class EvidenceTools:
    """One inspection's scoped tools and audit state; not shared across threads."""

    def __init__(self, evidence: EvidenceSnapshot, *, agent: MetaAgent | None = None,
                 limits: RetrievalLimits | None = None, journal: RunJournal | None = None,
                 cancelled: CancellationEvent | None = None):
        policy = evidence.manifest["policy"]
        if not policy["redact"] or not policy["hide_ranks"]:
            raise ValueError("Inspection tools require redacted, rank-free evidence")
        self.evidence = evidence
        self.limits = limits or RetrievalLimits()
        self.journal = journal
        self.cancelled = cancelled or CancellationEvent()
        self._events = {e["id"]: canonical_json(e) for e in evidence.iter_events()}
        self._sessions = {s["id"] for s in evidence.sessions}
        self._agent = canonical_json(redact(hide_historical_ranks(agent.without_ranks().model_dump(mode="json")))) \
            if agent is not None else None
        self._secret = secrets.token_bytes(32)
        self._started = time.monotonic()
        self._calls, self._chars = 0, 0
        self._audit: list[str] = []

    @property
    def schemas(self) -> list[dict]:
        return [{"name": name, "description": description,
                 "inputSchema": json.loads(canonical_json({"type": "object", "properties": properties,
                                                            "required": required, "additionalProperties": False}))}
                for name, (properties, required, description) in _SPEC.items()]

    @property
    def audit(self) -> list[dict]:
        return [json.loads(record) for record in self._audit]

    def call(self, name: str, arguments: dict) -> dict:
        try:
            return self._call(name, arguments)
        except EvidenceToolError as exc:
            # Rejected requests deliver no evidence. Do not persist arbitrary rejected inputs.
            record = {"tool": name if name in _SPEC else "unknown", "error": exc.code,
                      "snapshot_sha256": self.evidence.sha256}
            if self.journal:
                self.journal.record(self.evidence.sha256, "evidence_rejected", **record)
            self._audit.append(canonical_json(record))
            raise

    def _call(self, name: str, arguments: dict) -> dict:
        if self.cancelled.is_set():
            raise EvidenceToolError("cancelled", "Inspection was cancelled")
        if time.monotonic() - self._started > self.limits.timeout_seconds:
            raise EvidenceToolError("timeout", "Retrieval deadline exceeded")
        self._calls += 1
        if self._calls > self.limits.max_calls:
            raise EvidenceToolError("budget_exhausted", "Retrieval call budget exhausted")
        if name not in _SPEC:
            raise EvidenceToolError("unknown_tool", "Tool is not allowed")
        properties, required, _ = _SPEC[name]
        if not isinstance(arguments, dict) or set(arguments) - properties.keys() or set(required) - arguments.keys():
            raise EvidenceToolError("invalid_arguments", "Unknown or missing tool arguments")
        for key, value in arguments.items():
            _validate(value, properties[key])
        result = getattr(self, "_" + name)(**arguments)
        result["snapshot_sha256"] = self.evidence.sha256
        serialized = canonical_json(result)
        size = len(serialized)
        if size > self.limits.max_response_chars:
            raise EvidenceToolError("response_too_large", "Reduce limit or max_chars and retry")
        if self._chars + size > self.limits.max_total_chars:
            raise EvidenceToolError("budget_exhausted", "Retrieval character budget exhausted")
        record = {"tool": name, "arguments": redact(arguments), "result": result, "response_chars": size,
                  "response_sha256": content_hash(result)}
        if self.journal:
            self.journal.record(self.evidence.sha256, "evidence_delivered", **record)
        self._chars += size
        self._audit.append(canonical_json(record))
        return result

    def _page(self, rows, query, cursor, limit):
        fingerprint = content_hash([self.evidence.sha256, query])
        offset = 0
        if cursor is not None:
            try:
                raw, signature = cursor.split(".")
                if not hmac.compare_digest(signature, hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()):
                    raise ValueError("Invalid cursor signature")
                decoded = json.loads(base64.urlsafe_b64decode(raw.encode()))
                if decoded["query"] != fingerprint or type(decoded["offset"]) is not int or decoded["offset"] < 0:
                    raise ValueError("Invalid cursor scope")
                offset = decoded["offset"]
            except (ValueError, KeyError, TypeError) as exc:
                raise EvidenceToolError("invalid_cursor", "Cursor belongs to another query or inspection") from exc
        selected = rows[offset:offset + limit]
        next_cursor = None
        if offset + limit < len(rows):
            raw = base64.urlsafe_b64encode(canonical_json({"query": fingerprint, "offset": offset + limit}).encode())
            next_cursor = raw.decode() + "." + hmac.new(self._secret, raw, hashlib.sha256).hexdigest()
        return {"items": selected, "next_cursor": next_cursor}

    def _list_sessions(self, cursor=None, limit=20):
        rows = [{k: s[k] for k in ("id", "harness", "native_id", "event_count")} for s in self.evidence.sessions]
        return self._page(rows, ["sessions"], cursor, limit)

    def _search_events(self, query, session_ids=None, kinds=None, cursor=None, limit=20):
        scope = self._sessions if session_ids is None else set(session_ids)
        if scope - self._sessions:
            raise EvidenceToolError("out_of_scope", "Unknown or unselected session")
        rows = []
        for event in self.evidence.iter_events():
            if event["session_id"] not in scope or (kinds is not None and event["kind"] not in kinds):
                continue
            # Canonical JSON is also what read_event returns; offsets have one consistent meaning.
            text = self._events[event["id"]]
            match = re.search(re.escape(query), text, re.IGNORECASE)
            if match is None:
                continue
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 160)
            rows.append({"session_id": event["session_id"], "event_id": event["id"], "kind": event["kind"],
                         "snippet": text[start:end], "range": [start, end],
                         "complete": start == 0 and end == len(text)})
        return self._page(rows, ["search", query, sorted(scope), kinds], cursor, limit)

    def _event(self, session_id, event_id):
        event = self._events.get(event_id)
        if event is None or json.loads(event)["session_id"] != session_id:
            raise EvidenceToolError("out_of_scope", "Event does not belong to the selected session")
        return event

    @staticmethod
    def _read(text, offset, max_chars):
        if offset > len(text):
            raise EvidenceToolError("invalid_arguments", "Offset is beyond content")
        end = min(len(text), offset + max_chars)
        return {"content": text[offset:end], "range": [offset, end], "total_chars": len(text),
                "next_offset": end if end < len(text) else None,
                "content_sha256": hashlib.sha256(text.encode()).hexdigest()}

    def _read_event(self, session_id, event_id, offset=0, max_chars=4000):
        return {"session_id": session_id, "event_id": event_id,
                **self._read(self._event(session_id, event_id), offset, max_chars)}

    def _read_context(self, session_id, event_id, before=2, after=2, cursor=None, limit=20):
        self._event(session_id, event_id)
        groups = [g for g in event_groups(self.evidence) if g[0]["session_id"] == session_id]
        index = next(i for i, group in enumerate(groups) if any(e["id"] == event_id for e in group))
        rows = [{"session_id": session_id, "event_ids": [e["id"] for e in group],
                 "call_id": group[0].get("call_id"), "run_id": group[0]["run_id"]}
                for group in groups[max(0, index - before):index + after + 1]]
        return self._page(rows, ["context", session_id, event_id, before, after], cursor, limit)

    def _catalog(self):
        from thearc.learning.reflection.engine import resource_catalog

        return resource_catalog(MetaAgent.model_validate_json(self._agent)) if self._agent else []

    def _list_resources(self, cursor=None, limit=20):
        return self._page([{"target": item["target"], "sha256": item["sha256"]} for item in self._catalog()],
                          ["resources", content_hash(self._agent)], cursor, limit)

    def _read_resource(self, kind, name, section=None, offset=0, max_chars=4000):
        if not any(item["target"]["kind"] == kind and item["target"]["name"] == name for item in self._catalog()):
            raise EvidenceToolError("out_of_scope", "Unknown configuration target")
        target = ResourceTarget(kind=kind, name=name, section=section)
        agent = MetaAgent.model_validate_json(self._agent)
        try:
            content = redact(hide_historical_ranks(agent.read_target(target)))
            if kind == "skill" and isinstance(content, dict):
                content.pop("files", None)
        except (KeyError, ValueError) as exc:
            raise EvidenceToolError("invalid_target", "Resource section does not resolve") from exc
        return {"target": target.model_dump(), **self._read(canonical_json(content), offset, max_chars)}


def session_tools(evidence: EvidenceSnapshot, **kwargs) -> EvidenceTools:
    return EvidenceTools(evidence, **kwargs)
