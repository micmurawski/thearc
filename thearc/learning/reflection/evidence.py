"""Model-facing views of sanitized evidence; indexed records remain unchanged."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

EvidenceView = Literal["compact", "detailed"]

SELECTION_POLICY = (
    "Prioritize the first user message and last assistant message, then take whole "
    "call groups in supplied order up to the event limit; preserve supplied order. "
    "Earlier filtering or limits may also have omitted events."
)


def evidence_view(evidence: dict[str, Any], view: EvidenceView = "compact") -> dict[str, Any]:
    """Project prepared evidence without source harness metadata in either view.

    Compact citations use canonical event IDs. Session boundaries and known tool
    relationships survive the removal of storage/provenance identifiers.
    Detailed adds audit context, not more event content or a larger selection.
    """
    if view not in {"compact", "detailed"}:
        raise ValueError(f"Unknown evidence view: {view}")
    result = deepcopy(evidence)
    for session in result["sessions"]:
        session.pop("harness", None)
    if view == "detailed":
        return result

    for resource in result["resources"]:
        resource.pop("sha256", None)
    result["selection_policy"] = SELECTION_POLICY
    for session in result["sessions"]:
        calls: dict[tuple[str, str], list[str]] = {}
        runs = list(dict.fromkeys(event["run_id"] for event in session["events"]))
        for event in session["events"]:
            if event["kind"] == "tool_call" and event.get("call_id"):
                calls.setdefault((event["run_id"], event["call_id"]), []).append(event["id"])
        events = []
        for event in session["events"]:
            item = {key: event[key] for key in (
                "id", "kind", "role", "text", "tool_name", "status", "arguments_json",
            ) if event.get(key) is not None and event[key] != ""}
            if event.get("truncated"):
                item["truncated"] = True
            if len(runs) > 1:
                item["actor"] = f"agent-{runs.index(event['run_id']) + 1}"
            if event["kind"] == "tool_result" and event.get("call_id"):
                related = calls.get((event["run_id"], event["call_id"]), [])
                if related:
                    item["call_event_ids"] = related
            events.append(item)
        coverage = {
            "included_events": len(events),
            "omitted_events": len(session["omitted_event_ids"]),
            "truncated_events": len(session["truncated_event_ids"]),
        }
        session.clear()
        session.update(events=events, coverage=coverage)
    return result
