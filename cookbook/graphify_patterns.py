"""Example sequence matcher that retains commentary between a failure and retry."""

from thearc.learning.sessions.patterns import BehavioralPattern, PatternMatch


class ToolRetryAfterFailure(BehavioralPattern):
    name = "tool_retry_after_failure"
    description = "A failed tool call is followed by another call to the same tool, possibly after commentary."

    def __init__(self, max_span: int = 20):
        if type(max_span) is not int or max_span < 3:
            raise ValueError("max_span must be an integer >= 3")
        self.max_span = max_span

    def find_matches(self, events):
        matches, calls = [], {}
        failed = None
        for index, event in enumerate(events):
            if event.kind == "message" and event.role == "user":
                calls.clear()
                failed = None
            elif event.kind == "tool_call":
                if failed is not None:
                    start, call = failed
                    if call.tool_name == event.tool_name and index - start + 1 <= self.max_span:
                        matches.append(
                            PatternMatch(
                                pattern_name=self.name,
                                description=self.description,
                                session_id=event.session_id,
                                start_index=start,
                                end_index=index,
                                events=tuple(events[start : index + 1]),
                                context={"tool": call.tool_name, "max_span": self.max_span},
                                severity="info",
                            )
                        )
                failed = None  # Only the next tool call can complete this sequence.
                if event.call_id and event.tool_name:
                    calls[event.call_id] = (index, event)
            elif event.kind == "tool_result" and event.status in {"failure", "error"}:
                failed = calls.get(event.call_id)
        return matches
