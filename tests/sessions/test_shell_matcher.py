import json

import pytest

from thearc.sessions import Session
from thearc.sessions.scanner import PatternScanner, ShellCommand

from .conftest import event


def call(number=0, *, tool="Bash", arguments=None, **fields):
    return event(number, kind="tool_call", tool_name=tool, arguments=arguments, call_id=f"c{number}", **fields)


@pytest.mark.parametrize(("tool", "arguments"), [
    ("Bash", {"command": "graphify query route"}),
    ("functions.exec_command", {"cmd": "graphify query route"}),
    ("shell", {"command": ["graphify", "query", "route"]}),
    ("run_command", {"CommandLine": "graphify query route"}),
    ("bash", json.dumps({"command": "graphify query route"})),
    ("bash", "graphify query route"),
    ("shell", {"command": ["/bin/bash", "-lc", "graphify query route"]}),
    ("custom", {"script": "graphify query route"}),
])
def test_native_shell_formats(tool, arguments):
    session = Session.from_events([call(tool=tool, arguments=arguments, action_kind="shell.exec")])
    (match,) = session.match(ShellCommand(startswith="graphify", contains="route"))
    assert match.context["command"] == "graphify query route"
    assert match.context["status"] == "unknown" and not match.context["wrapped"]


def test_filters_are_literal_case_sensitive_and_combined_with_and():
    events = [call(arguments={"command": " \nGraphify query route | head -5"})]
    assert not ShellCommand(startswith="graphify").find_matches(events)
    assert ShellCommand(startswith="graphify", case_sensitive=False).find_matches(events)
    assert ShellCommand(contains="| head").find_matches(events)
    assert not ShellCommand(startswith="head").find_matches(events)
    assert not ShellCommand(startswith="Graphify", contains="update").find_matches(events)
    assert not ShellCommand(contains="query.*route").find_matches(events)


@pytest.mark.parametrize("arguments", [{}, {"command": None}, {"command": []}, {"command": [1, 2]}])
def test_missing_commands_are_ignored(arguments):
    assert not ShellCommand(contains="graphify").find_matches([call(arguments=arguments)])


@pytest.mark.parametrize("options", [{}, {"startswith": ""}, {"contains": " "}, {"contains": 1}])
def test_invalid_filters(options):
    with pytest.raises(ValueError):
        ShellCommand(**options)


def test_does_not_match_chat_results_or_unrelated_tool_arguments():
    events = [event(0, text="graphify query"), event(1, kind="tool_result", text="graphify query"),
              call(2, tool="Read", arguments={"command": "graphify query"}),
              call(3, arguments={"command": "pwd", "description": "graphify query"})]
    assert not ShellCommand(contains="graphify").find_matches(events)


def test_pairs_nonadjacent_result_without_crossing_runs_and_keeps_reported_outcome():
    events = [call(arguments={"cmd": "graphify query", "workdir": "/historic"}),
              event(1, text="waiting"),
              event(2, kind="tool_result", call_id="c0", status="failure", text="Requires approval"),
              event(3, kind="tool_result", call_id="c0", status="success").model_copy(update={"run_id": "another"})]
    (match,) = ShellCommand(contains="graphify").find_matches(events)
    assert match.event_ids == ["e0", "e1", "e2"]
    assert match.context["status"] == "failure"
    assert match.context["cwd"] == "/historic"
    assert match.context["result_event_id"] == "e2"


def test_missing_call_id_does_not_pair_unrelated_result():
    events = [call(arguments={"cmd": "graphify query"}).model_copy(update={"call_id": None}),
              event(1, kind="tool_result", status="success")]
    (match,) = ShellCommand(contains="graphify").find_matches(events)
    assert match.context["status"] == "unknown" and match.event_ids == ["e0"]


def test_wrapped_commands_extract_literals_and_do_not_inherit_wrapper_success():
    code = '''
text(await tools.exec_command({cmd: "graphify query route", workdir: "/repo", max_output_tokens: 100}));
text(await tools.exec_command({"cmd": 'bash scripts/check.sh', "cwd": "/elsewhere"}));
text(await tools.exec_command({cmd: `graphify update .\n` }));
'''
    events = [call(tool="functions.exec", arguments=code),
              event(1, kind="tool_result", call_id="c0", status="success")]
    matches = ShellCommand(contains="graphify").find_matches(events)
    assert [match.context["command"] for match in matches] == ["graphify query route", "graphify update .\n"]
    assert matches[0].context["cwd"] == "/repo"
    assert [match.context["command_index"] for match in matches] == [0, 2]
    assert all(match.context["status"] == "unknown" and match.context["wrapped"] for match in matches)
    assert all(match.context["wrapper_status"] == "success" for match in matches)
    assert len(ShellCommand(startswith="bash scripts/").find_matches(events)) == 1


@pytest.mark.parametrize("code", [
    '// tools.exec_command({cmd: "graphify query"})',
    '/* tools.exec_command({cmd: "graphify query"}) */',
    '''const example = 'tools.exec_command({cmd: "graphify query"})';''',
    'tools.exec_command({cmd: command})',
    'tools.exec_command({cmd: "graphify " + command})',
    'tools.exec_command({cmd: `graphify ${command}`})',
    'tools.exec_command({cmd: "graphify query", ...overrides})',
    'tools.exec_command({cmd: "graphify query", cmd: other})',
    'tools.exec_command({description: "graphify query", cmd: "pwd"})',
])
def test_wrapped_examples_and_dynamic_commands_are_not_guessed(code):
    assert not ShellCommand(contains="graphify").find_matches([call(tool="exec", arguments=code)])


def test_scanner_and_trajectory_use_the_same_matcher():
    events = [event(0, role="user", text="Query"), call(1, arguments={"command": "graphify query"}),
              event(2, kind="tool_result", call_id="c1", status="success")]
    matcher = ShellCommand(startswith="graphify")
    session = Session.from_events(events)
    assert session.match(matcher)[0].context == session.trajectories[0].match(matcher)[0].context
    assert PatternScanner(patterns=[matcher]).scan_events(events)[0].event_ids == ["e1", "e2"]
