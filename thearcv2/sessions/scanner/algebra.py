"""Compose evidence-producing matchers; AND always has an explicit temporal scope."""

from __future__ import annotations

from dataclasses import replace
from itertools import groupby
from typing import Any, Literal

from thearcv2.sessions.models import Event
from thearcv2.sessions.trajectories import split_trajectories

from .patterns import BehavioralPattern, PatternMatch, _stream_key

Scope = Literal["trajectory", "command"]


def _unique(matches: list[PatternMatch]) -> list[PatternMatch]:
    """Keep different judgments of the same span, but drop identical findings."""
    buckets: dict[tuple[str, int, int], list[PatternMatch]] = {}
    result = []
    for match in matches:
        bucket = buckets.setdefault((match.pattern_name, match.start_index, match.end_index), [])
        if match not in bucket:
            bucket.append(match)
            result.append(match)
    return sorted(result, key=lambda match: (match.start_index, match.end_index))


def _evidence(match: PatternMatch) -> dict[str, Any]:
    # Event IDs survive rebasing by Session.match / PatternScanner. Nested local
    # indices would become incorrect when those callers shift the outer match.
    return {"pattern_name": match.pattern_name, "description": match.description,
            "event_ids": match.event_ids, "severity": match.severity, "context": match.context}


class _Composite(BehavioralPattern):
    def __init__(self, *patterns: BehavioralPattern):
        if not patterns:
            raise ValueError("provide at least one pattern")
        if any(not isinstance(pattern, BehavioralPattern) for pattern in patterns):
            raise TypeError("operands must be BehavioralPattern instances")
        self.patterns = patterns

    def find_matches(self, events: list[Event]) -> list[PatternMatch]:
        # Preserve the same boundaries even when called without Session or Scanner.
        matches = []
        offset = 0
        for _, group in groupby(events, key=_stream_key):
            segment = list(group)
            for match in self._match_segment(segment):
                matches.append(replace(match, start_index=match.start_index + offset,
                                       end_index=match.end_index + offset))
            offset += len(segment)
        return matches

    def _match_segment(self, events: list[Event]) -> list[PatternMatch]:
        raise NotImplementedError


class AnyOf(_Composite):
    """Union of the operands' findings, ordered by position and deduplicated.

    Original findings retain their names, context and severity. Different
    findings at the same position remain distinct. ``a | b`` is shorthand.
    """

    name = "any_of"
    description = "At least one operand matched."

    def _match_segment(self, events: list[Event]) -> list[PatternMatch]:
        return _unique([match for pattern in self.patterns for match in pattern.find_matches(events)])


class AllOf(_Composite):
    """All operands must have findings wholly within the same scope.

    ``trajectory`` (default, also ``a & b``): one combined finding per user
    prompt/response with at least one match from each operand. Unassigned events
    and findings crossing a trajectory boundary do not satisfy this scope.

    ``command``: one combined finding per shell command occurrence, identified
    by its call event and ``command_index``. Supports ShellCommand, shell-based
    SkillUsage, and command AllOf results (including inside AnyOf); unrelated types do not supply
    a command identity. Two commands in one wrapper remain distinct.

    Each result contains the enclosing event slice, the highest child severity,
    and ``context['operands']`` with all supporting findings grouped by operand.
    Combining N findings does not generate their Cartesian product. Nested
    composites preserve the scopes chosen by their constructors.
    """

    name = "all_of"
    description = "Every operand matched within the same scope."

    def __init__(self, *patterns: BehavioralPattern, scope: Scope = "trajectory"):
        super().__init__(*patterns)
        if scope not in {"trajectory", "command"}:
            raise ValueError("scope must be 'trajectory' or 'command'")
        self.scope = scope

    def _match_segment(self, events: list[Event]) -> list[PatternMatch]:
        found = [_unique(pattern.find_matches(events)) for pattern in self.patterns]
        if any(not matches for matches in found):
            return []
        positions = {event.id: index for index, event in enumerate(events)}
        bounds: dict[str, tuple[int, int]] = {}
        if self.scope == "trajectory":
            for trajectory in split_trajectories(events):
                selected = trajectory.selected_events
                bounds[trajectory.id] = (positions[selected[0].id], positions[selected[-1].id])

        def key(match: PatternMatch) -> tuple | None:
            if self.scope == "trajectory":
                return next(((identifier,) for identifier, (start, end) in bounds.items()
                             if start <= match.start_index <= match.end_index <= end), None)
            command_index = match.context.get("command_index")
            if type(command_index) is not int or command_index < 0:
                return None
            start = events[match.start_index]
            if start.kind != "tool_call":
                return None
            return start.id, command_index

        grouped: list[dict[tuple, list[PatternMatch]]] = []
        for matches in found:
            groups: dict[tuple, list[PatternMatch]] = {}
            for match in matches:
                identity = key(match)
                if identity is not None:
                    groups.setdefault(identity, []).append(match)
            grouped.append(groups)
        common = set(grouped[0]).intersection(*(set(groups) for groups in grouped[1:]))
        results = []
        for identity in common:
            members = [groups[identity] for groups in grouped]
            supporting = [match for matches in members for match in matches]
            start = min(match.start_index for match in supporting)
            end = max(match.end_index for match in supporting)
            severity = max((match.severity for match in supporting),
                           key=lambda value: {"info": 0, "warning": 1, "error": 2}.get(value, 0))
            context: dict[str, Any] = {
                "scope": self.scope,
                "operands": [{"pattern_name": pattern.name, "matches": [_evidence(match) for match in matches]}
                             for pattern, matches in zip(self.patterns, members)],
            }
            if self.scope == "trajectory":
                context["trajectory_id"] = identity[0]
            else:
                context.update(command_event_id=identity[0], command_index=identity[1])
            results.append(PatternMatch(
                pattern_name=self.name, description=f"All {len(self.patterns)} patterns matched in one {self.scope}.",
                session_id=events[start].session_id, start_index=start, end_index=end,
                events=tuple(events[start:end + 1]), context=context, severity=severity,
            ))
        return sorted(results, key=lambda match: (match.start_index, match.end_index,
                                                 match.context.get("command_index", -1)))
