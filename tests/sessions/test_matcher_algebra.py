"""Composition preserves scope, evidence, and the public scanner/session contracts."""

import pytest

from thearc.sessions import Session
from thearc.sessions.scanner import (
    AllOf,
    AnyOf,
    BehavioralPattern,
    FrustrationSpike,
    PatternMatch,
    PatternScanner,
    ShellCommand,
)

from .conftest import event


def command(n, script):
    return event(n, kind="tool_call", tool_name="bash", arguments={"command": script}, call_id=f"c{n}")


def prompt(n):
    return event(n, role="user", text=f"task {n}")


def outcome(n, call, status):
    return event(n, kind="tool_result", call_id=f"c{call}", status=status)


QUERY = ShellCommand(startswith="graphify query")
TEST = ShellCommand(startswith="pytest")
UPDATE = ShellCommand(startswith="graphify update")


def test_or_unions_in_order_and_deduplicates_identical_findings():
    events = [command(0, "pytest"), command(1, "graphify query route"), command(2, "pwd")]
    matches = AnyOf(QUERY, TEST, ShellCommand(contains="graphify")).find_matches(events)
    assert [match.start_index for match in matches] == [0, 1]
    assert [match.pattern_name for match in matches] == ["shell_command", "shell_command"]
    assert (QUERY | TEST | QUERY).find_matches(events) == matches
    assert AnyOf(QUERY | TEST, QUERY).find_matches(events) == matches


def test_or_keeps_distinct_judgments_at_the_same_position():
    class Judgment(BehavioralPattern):
        def __init__(self, rating):
            self.rating = rating

        def find_matches(self, events):
            return [PatternMatch("judgment", "Judged", events[0].session_id, 0, 0,
                                 (events[0],), context={"rating": self.rating})]

    matches = AnyOf(Judgment("helpful"), Judgment("harmful")).find_matches([prompt(0)])
    assert [match.context["rating"] for match in matches] == ["helpful", "harmful"]


def test_and_groups_all_supporting_evidence_once_per_trajectory():
    events = [prompt(0), command(1, "graphify query route"), command(2, "pytest tests/a.py"),
              command(3, "pytest tests/b.py"), prompt(4), command(5, "pytest")]
    (match,) = AllOf(QUERY, TEST, scope="trajectory").find_matches(events)
    assert match.context["trajectory_id"] == "e0"
    assert match.event_ids == ["e1", "e2", "e3"]
    first, second = match.context["operands"]
    assert first["matches"][0]["event_ids"] == ["e1"]
    assert [finding["event_ids"] for finding in second["matches"]] == [["e2"], ["e3"]]
    assert (QUERY & TEST).find_matches(events) == [match]
    assert (TEST & QUERY).find_matches(events)[0].event_ids == match.event_ids


def test_and_does_not_cross_prompts_or_use_unassigned_events():
    assert not (QUERY & TEST).find_matches([prompt(0), command(1, "graphify query route"),
                                          prompt(2), command(3, "pytest")])
    assert not (QUERY & TEST).find_matches([command(0, "graphify query route"), prompt(1), command(2, "pytest")])
    assert not (QUERY & TEST).find_matches([command(0, "graphify query route"), command(1, "pytest")])


def test_and_respects_explicit_completion_and_crossing_match_boundaries():
    events = [prompt(0), command(1, "graphify query route"),
              event(2, kind="metadata", raw={"type": "event_msg", "payload": {"type": "task_complete"}}),
              command(3, "pytest")]
    events = [e.model_copy(update={"harness": "codex"}) for e in events]
    assert not (QUERY & TEST).find_matches(events)
    # A shell result arriving after the next prompt makes its match cross the boundary.
    events = [prompt(0), command(1, "graphify query route"), prompt(2), command(3, "pytest"),
              outcome(4, 1, "success")]
    assert not (QUERY & TEST).find_matches(events)


@pytest.mark.parametrize("field,value", [("run_id", "child"), ("session_id", "other"),
                                        ("source_id", "other")])
def test_direct_composition_does_not_join_unrelated_streams(field, value):
    events = [prompt(0), command(1, "graphify query route"),
              command(2, "pytest").model_copy(update={field: value})]
    assert not (QUERY & TEST).find_matches(events)
    assert [m.start_index for m in (QUERY | TEST).find_matches(events)] == [1, 2]


def test_nested_and_or_and_operator_precedence():
    events = [prompt(0), command(1, "graphify update ."), command(2, "pytest"),
              prompt(3), command(4, "graphify query route")]
    matches = ((QUERY | UPDATE) & TEST).find_matches(events)
    assert len(matches) == 1 and matches[0].event_ids == ["e1", "e2"]
    # Python's & binds more tightly than |.
    matches = (QUERY | UPDATE & TEST).find_matches(events)
    assert [m.event_ids for m in matches] == [["e1", "e2"], ["e4"]]
    events = [prompt(0), command(1, "graphify query route"), command(2, "pytest"), command(3, "graphify update .")]
    assert (QUERY & TEST & UPDATE).find_matches(events)[0].event_ids == ["e1", "e2", "e3"]


def test_and_propagates_highest_severity_and_keeps_individual_outcomes():
    events = [prompt(0), command(1, "pytest"), outcome(2, 1, "failure")]
    (match,) = (TEST & FrustrationSpike(window=1, threshold=1)).find_matches(events)
    assert match.severity == "warning"
    assert match.context["operands"][0]["matches"][0]["context"]["status"] == "failure"


def test_command_scope_requires_one_command_including_inside_wrappers():
    matcher = AllOf(ShellCommand(startswith="graphify"), ShellCommand(contains="route"), scope="command")
    assert not matcher.find_matches([command(0, "graphify update ."), command(1, "echo route")])
    (match,) = matcher.find_matches([command(0, "graphify query route")])
    assert match.context["command_event_id"] == "e0" and match.context["command_index"] == 0
    wrapper = event(0, kind="tool_call", tool_name="exec", arguments='''
tools.exec_command({cmd: "graphify update ."});
tools.exec_command({cmd: "echo route"});
''')
    assert not matcher.find_matches([wrapper])
    wrapper = wrapper.model_copy(update={"arguments": wrapper.arguments +
                                        '\ntools.exec_command({cmd: "graphify query route"});'})
    (match,) = matcher.find_matches([wrapper])
    assert match.context["command_index"] == 2
    assert AllOf(matcher, QUERY, scope="command").find_matches([wrapper])[0].context["command_index"] == 2
    assert AllOf(QUERY | TEST, matcher, scope="command").find_matches([wrapper])


def test_command_scope_does_not_join_matches_without_command_identity():
    events = [prompt(0), command(1, "pytest"), outcome(2, 1, "failure")]
    assert not AllOf(TEST, FrustrationSpike(window=1, threshold=1), scope="command").find_matches(events)


def test_public_apis_preserve_indices_and_nested_evidence_after_rebasing():
    events = [prompt(0), command(1, "pwd"), prompt(2), command(3, "graphify query route"), command(4, "pytest")]
    events = [e.model_copy(update={"run_id": "child"}) if i >= 2 else e for i, e in enumerate(events)]
    session = Session.from_events(events)
    matcher = QUERY & TEST
    direct = matcher.find_matches(events)
    public = session.match(matcher)
    scanned = PatternScanner(patterns=[matcher]).scan_events(events)
    assert direct == scanned
    assert public[0].start_index == 3 and public[0].end_index == 4
    assert public[0].context == direct[0].context
    assert public[0].context["operands"][0]["matches"][0]["event_ids"] == ["e3"]
    assert session.trajectories[-1].match(matcher)[0].start_index == 1


@pytest.mark.parametrize("factory", [AnyOf, AllOf])
def test_composite_validation_empty_input_and_single_operand(factory):
    with pytest.raises(ValueError, match="at least one"):
        factory()
    with pytest.raises(TypeError, match="BehavioralPattern"):
        factory(QUERY, "pytest")
    assert factory(QUERY).find_matches([]) == []
    assert factory(QUERY).find_matches([prompt(0), command(1, "graphify query route")])


def test_bad_scope_and_boolean_operators_fail_clearly():
    with pytest.raises(ValueError, match="scope"):
        AllOf(QUERY, TEST, scope="session")
    with pytest.raises(TypeError, match="no truth value"):
        _ = QUERY and TEST
    with pytest.raises(TypeError, match="no truth value"):
        _ = QUERY or TEST
    with pytest.raises(TypeError, match="BehavioralPattern"):
        QUERY | 42
