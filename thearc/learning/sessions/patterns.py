"""Behavioral pattern contracts and Python sequence matchers.

StubbornToolLoop and FrustrationSpike are the scanner defaults. The remaining
heuristics are available for explicit selection. Matches are signals for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from thearc.learning.sessions.models import Event


@dataclass(frozen=True, slots=True)
class PatternMatch:
    """A single detected behavioral pattern occurrence."""

    pattern_name: str
    """Name of the pattern that matched."""

    description: str
    """Human-readable explanation of this specific match."""

    session_id: str
    """Session where the match was found."""

    start_index: int
    """Index into the event list where the match begins."""

    end_index: int
    """Index into the event list where the match ends (inclusive)."""

    events: tuple[Event, ...]
    """The exact slice of events constituting the match."""

    context: dict[str, Any] = field(default_factory=dict)
    """Pattern-specific metadata (e.g. tool name, file path, error count)."""

    severity: str = "info"
    """One of ``info``, ``warning``, ``error``. Defaults to ``info``."""

    @property
    def event_ids(self) -> list[str]:
        return [e.id for e in self.events]

    @property
    def span(self) -> int:
        return self.end_index - self.start_index + 1


class BehavioralPattern:
    """Base class for all behavioral patterns.

    Subclasses must override :meth:`find_matches` and set the ``name``
    and ``description`` class attributes.
    """

    name: str = "unnamed_pattern"
    description: str = ""

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        """Scan ordered events and return all detected matches."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Built-in patterns
# ---------------------------------------------------------------------------


def _stream_key(event: Event) -> tuple:
    """Native ordering is meaningful only within the same run and artifact."""
    return (event.source_id, event.session_id, event.run_id,
            event.reference.path, event.reference.generation)


def _same_stream(events: list[Event]) -> bool:
    return all(_stream_key(event) == _stream_key(events[0]) for event in events)


def _positive_integer(name: str, value: int, minimum: int = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _failed_result(call: Event, result: Event) -> bool:
    if result.kind != "tool_result" or result.status not in {"failure", "error"}:
        return False
    if call.call_id != result.call_id:
        return False
    return result.tool_name is None or result.tool_name == call.tool_name


class StubbornToolLoop(BehavioralPattern):
    """Adjacent call → failed result → same named tool call.

    ``min_repeats`` counts calls (default 2). Each preceding call must fail;
    the last call need not have a result yet. Arguments may differ. Overlapping
    windows are returned separately. Known call IDs must agree with the result.
    """

    name = "stubborn_tool_loop"
    description = "Agent retries the same named tool immediately after a failure."

    def __init__(self, min_repeats: int = 2) -> None:
        _positive_integer("min_repeats", min_repeats, minimum=2)
        self.min_repeats = min_repeats

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        matches: list[PatternMatch] = []
        size = 2 * self.min_repeats - 1
        for i in range(len(events) - size + 1):
            window = events[i:i + size]
            tool = window[0].tool_name
            if not tool or not _same_stream(window):
                continue
            if any(call.kind != "tool_call" or call.tool_name != tool for call in window[::2]):
                continue
            if not all(_failed_result(window[j], window[j + 1]) for j in range(0, size - 1, 2)):
                continue
            matches.append(PatternMatch(
                pattern_name=self.name,
                description=f"Tool '{tool}' called {self.min_repeats} times with intervening failures.",
                session_id=window[0].session_id,
                start_index=i,
                end_index=i + size - 1,
                events=tuple(window),
                context={"tool": tool, "cycles": self.min_repeats},
                severity="warning",
            ))
        return matches


class FrustrationSpike(BehavioralPattern):
    """Many errors concentrated in a short window.

    Parameters
    ----------
    window:
        Number of events to consider in each sliding window (default 5).
    threshold:
        Minimum error count within the window to trigger (default 3).
    """

    name = "frustration_spike"
    description = "High density of errors within a short event window."

    def __init__(self, window: int = 5, threshold: int = 3) -> None:
        _positive_integer("window", window)
        _positive_integer("threshold", threshold)
        if threshold > window:
            raise ValueError("threshold must not exceed window")
        self.window = window
        self.threshold = threshold

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        matches: list[PatternMatch] = []
        for i in range(len(events) - self.window + 1):
            window = events[i: i + self.window]
            if not _same_stream(window):
                continue
            error_count = sum(1 for e in window if e.status in {"failure", "error"})
            if error_count >= self.threshold:
                matches.append(PatternMatch(
                    pattern_name=self.name,
                    description=f"{error_count} errors in a {self.window}-event window.",
                    session_id=events[i].session_id,
                    start_index=i,
                    end_index=i + self.window - 1,
                    events=tuple(window),
                    context={"error_count": error_count, "window_size": self.window},
                    severity="warning",
                ))
        return matches


class SelfOverwrite(BehavioralPattern):
    """Agent writes the same file multiple times consecutively.

    Detects when the agent issues back-to-back write/edit tool calls targeting
    the same file path without any intervening read, test, or user turn.

    Parameters
    ----------
    min_writes:
        Minimum number of consecutive writes to the same file (default 2).
    """

    name = "self_overwrite"
    description = "Agent writes the same file multiple times without verifying."

    _WRITE_TOOLS = frozenset({
        "write_to_file", "replace_file_content", "create_file", "edit_file",
        "write_file", "file_editor", "str_replace_editor",
    })

    def __init__(self, min_writes: int = 2) -> None:
        _positive_integer("min_writes", min_writes)
        self.min_writes = min_writes

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        matches: list[PatternMatch] = []
        i = 0
        while i < len(events):
            e = events[i]
            if e.kind != "tool_call" or not self._is_write(e):
                i += 1
                continue
            target_file = self._extract_file(e)
            if not target_file:
                i += 1
                continue
            streak_start = i
            streak: list[Event] = [e]
            j = i + 1
            while j < len(events):
                ej = events[j]
                # Skip tool results (they're paired with the call)
                if ej.kind == "tool_result":
                    streak.append(ej)
                    j += 1
                    continue
                # Another write to the same file?
                if ej.kind == "tool_call" and self._is_write(ej) and self._extract_file(ej) == target_file:
                    streak.append(ej)
                    j += 1
                    continue
                # A break tool or user turn ends the streak
                break
            write_count = sum(1 for ev in streak if ev.kind == "tool_call")
            if write_count >= self.min_writes:
                matches.append(PatternMatch(
                    pattern_name=self.name,
                    description=f"File '{target_file}' written {write_count} times consecutively.",
                    session_id=events[streak_start].session_id,
                    start_index=streak_start,
                    end_index=streak_start + len(streak) - 1,
                    events=tuple(streak),
                    context={"file": target_file, "write_count": write_count},
                    severity="warning",
                ))
            i = j
        return matches

    def _is_write(self, event: Event) -> bool:
        tool = event.tool_name or event.action_kind or ""
        return tool.lower() in self._WRITE_TOOLS or "write" in tool.lower() or "edit" in tool.lower()

    @staticmethod
    def _extract_file(event: Event) -> str | None:
        args = event.arguments
        if isinstance(args, dict):
            for key in ("TargetFile", "target_file", "path", "file_path", "file", "filename"):
                val = args.get(key)
                if isinstance(val, str) and val:
                    return val
        if isinstance(args, str):
            # Try to find a file path in the argument string
            match = re.search(r'(?:^|["\s])(/\S+\.\w+)', args)
            if match:
                return match.group(1)
        return None


class IgnoredUserInstruction(BehavioralPattern):
    """Agent launches tool calls immediately after a user message without
    any assistant text acknowledging the instruction first.

    Parameters
    ----------
    max_gap:
        Maximum events after user message to check for assistant text (default 5).
    """

    name = "ignored_user_instruction"
    description = "User instruction followed by tool calls with no assistant acknowledgment."

    def __init__(self, max_gap: int = 5) -> None:
        _positive_integer("max_gap", max_gap)
        self.max_gap = max_gap

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        matches: list[PatternMatch] = []
        for i, e in enumerate(events):
            if e.role != "user" or e.kind != "message":
                continue
            if not e.text or not e.text.strip():
                continue
            # Look at the next max_gap events
            window = events[i + 1: i + 1 + self.max_gap]
            has_assistant_text = False
            has_tool_call = False
            span_events: list[Event] = [e]
            for w in window:
                span_events.append(w)
                if w.role == "assistant" and w.kind == "message" and w.text and w.text.strip():
                    has_assistant_text = True
                    break
                if w.kind == "tool_call":
                    has_tool_call = True
                if w.role == "user":
                    break  # next user turn
            if has_tool_call and not has_assistant_text:
                matches.append(PatternMatch(
                    pattern_name=self.name,
                    description="Agent jumped to tool calls without acknowledging user instruction.",
                    session_id=e.session_id,
                    start_index=i,
                    end_index=i + len(span_events) - 1,
                    events=tuple(span_events),
                    context={"user_text_preview": e.text[:100]},
                    severity="info",
                ))
        return matches


class EmptyToolResult(BehavioralPattern):
    """Tool result events with blank or missing output text."""

    name = "empty_tool_result"
    description = "Tool returned blank or no output."

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        matches: list[PatternMatch] = []
        for i, e in enumerate(events):
            if e.kind != "tool_result":
                continue
            if e.text and e.text.strip():
                continue
            tool = e.tool_name or e.action_kind or "unknown"
            matches.append(PatternMatch(
                pattern_name=self.name,
                description=f"Tool '{tool}' returned empty output.",
                session_id=e.session_id,
                start_index=i,
                end_index=i,
                events=(e,),
                context={"tool": tool, "status": e.status or "unknown"},
                severity="info",
            ))
        return matches


class AbandonedFix(BehavioralPattern):
    """Agent edits a file, gets an error, then moves on to a different file
    without resolving the original failure.

    Parameters
    ----------
    lookahead:
        How many events ahead to look for a resolution attempt (default 10).
    """

    name = "abandoned_fix"
    description = "Agent started fixing a file, hit an error, and moved on without resolving."

    _WRITE_TOOLS = SelfOverwrite._WRITE_TOOLS

    def __init__(self, lookahead: int = 10) -> None:
        _positive_integer("lookahead", lookahead)
        self.lookahead = lookahead

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        matches: list[PatternMatch] = []
        i = 0
        while i < len(events) - 2:
            e = events[i]
            if e.kind != "tool_call":
                i += 1
                continue
            tool = e.tool_name or e.action_kind or ""
            if tool.lower() not in self._WRITE_TOOLS:
                i += 1
                continue
            target_file = SelfOverwrite._extract_file(e)
            if not target_file:
                i += 1
                continue
            # The loop requires at least three remaining events.
            result = events[i + 1]
            if not _failed_result(e, result):
                i += 1
                continue
            # Look ahead: did the agent come back to fix target_file?
            came_back = False
            moved_to: int | None = None
            span_end = min(i + 2 + self.lookahead, len(events))
            for j in range(i + 2, span_end):
                ej = events[j]
                if _stream_key(ej) != _stream_key(e):
                    break
                if ej.kind == "tool_call":
                    ej_file = SelfOverwrite._extract_file(ej)
                    if ej_file == target_file:
                        came_back = True
                        break
                    if (moved_to is None and ej_file
                            and (ej.tool_name or "").lower() in self._WRITE_TOOLS):
                        moved_to = j
            if not came_back and moved_to is not None and _same_stream(events[i:moved_to + 1]):
                span = events[i:moved_to + 1]
                matches.append(PatternMatch(
                    pattern_name=self.name,
                    description=f"Edit to '{target_file}' failed; another file was edited within the lookahead window.",
                    session_id=e.session_id,
                    start_index=i,
                    end_index=i + len(span) - 1,
                    events=tuple(span),
                    context={"file": target_file, "tool": tool},
                    severity="warning",
                ))
            i += 2
        return matches


class LongSilence(BehavioralPattern):
    """No assistant text for many consecutive events (e.g. only tool calls).

    Parameters
    ----------
    threshold:
        Number of consecutive non-text events before triggering (default 8).
    """

    name = "long_silence"
    description = "Agent produced no text output for many consecutive events."

    def __init__(self, threshold: int = 8) -> None:
        _positive_integer("threshold", threshold)
        self.threshold = threshold

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        matches: list[PatternMatch] = []
        streak_start: int | None = None
        streak_count = 0
        for i, e in enumerate(events):
            is_assistant_text = (
                e.role == "assistant" and e.kind == "message"
                and e.text and e.text.strip()
            )
            is_user = e.role == "user"
            if is_assistant_text or is_user:
                if streak_count >= self.threshold and streak_start is not None:
                    span = events[streak_start: i]
                    matches.append(PatternMatch(
                        pattern_name=self.name,
                        description=f"No assistant text for {streak_count} consecutive events.",
                        session_id=events[streak_start].session_id,
                        start_index=streak_start,
                        end_index=i - 1,
                        events=tuple(span),
                        context={"silent_events": streak_count},
                        severity="info",
                    ))
                streak_start = None
                streak_count = 0
            else:
                if streak_start is None:
                    streak_start = i
                streak_count += 1
        # Trailing silence
        if streak_count >= self.threshold and streak_start is not None:
            span = events[streak_start:]
            matches.append(PatternMatch(
                pattern_name=self.name,
                description=f"No assistant text for {streak_count} consecutive events (end of session).",
                session_id=events[streak_start].session_id,
                start_index=streak_start,
                end_index=len(events) - 1,
                events=tuple(span),
                context={"silent_events": streak_count},
                severity="info",
            ))
        return matches
