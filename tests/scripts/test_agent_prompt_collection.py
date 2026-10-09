"""Exercise native capture and indexing without model calls or credentials."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_agent_prompts as collector
from scripts.prompt_harnesses import inspect_output
from thearc.learning.sessions.models import SearchFilters
from thearc.learning.sessions.store import SessionStore


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    (project / "AGENTS.md").write_text("Use the prepared agent.")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "baseline"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setenv("FAKE_NATIVE", str(native))
    fake = tmp_path / "agent"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, sys, time, uuid
from pathlib import Path
args = sys.argv[1:]
assert os.environ["PWD"] == str(Path.cwd())
mode = os.environ.get("FAKE_MODE", "success")
def emit(obj):
    print(json.dumps(obj), flush=True)
if args[:2] == ["session", "export"]:
    if mode == "export-failure":
        sys.exit(2)
    print(Path("export.json").read_text())
    sys.exit(0)
prompt = sys.stdin.read()
assert prompt == "Inspect files", repr(prompt)
assert Path("AGENTS.md").read_text() == "Use the prepared agent."
session = str(uuid.uuid4())
cwd = str(Path.cwd())
if mode == "wrong-cwd":
    cwd = "/another/project"
tool = {"type": "tool_use", "id": "call-1", "name": "bash", "input": {"command": "false"}}
text = {"type": "text", "text": "Done"}
parts = [tool, text] if mode != "response-only" else [text]
if "--session-dir" in args:
    root = Path(args[args.index("--session-dir") + 1])
    root.mkdir(parents=True)
    header = {"type": "session", "id": session, "cwd": cwd}
    emit(header)
    pi_parts = [{"type": "toolCall", "id": "call-1", "name": "bash", "arguments": {"command": "false"}}, text]
    message = {"role": "assistant", "content": pi_parts if mode != "response-only" else [text],
               "stopReason": "error" if mode == "failure" else "stop"}
    emit({"type": "message_end", "message": message})
    emit({"type": "agent_end", "willRetry": False})
    records = [header, {"type": "message", "message": {"role": "user", "content": prompt}},
               {"type": "message", "message": message},
               {"type": "message", "message": {"role": "toolResult", "toolCallId": "call-1",
                "toolName": "bash", "isError": True, "content": [{"type": "text", "text": "failed"}]}}]
    if mode == "response-only":
        records.pop()
elif "--session-id" in args:
    session = args[args.index("--session-id") + 1]
    root = Path(os.environ["FAKE_NATIVE"])
    emit({"type": "system", "subtype": "init", "session_id": session})
    message = {"role": "assistant", "content": parts}
    emit({"type": "assistant", "message": message, "session_id": session})
    emit({"type": "result", "subtype": "error_during_execution" if mode == "failure" else "success",
          "is_error": mode in {"failure", "auth"}, "session_id": session,
          "result": ("Failed to authenticate. API Error: 401 OAuth access token is invalid."
                     if mode == "auth" else "Done")})
    records = [{"type": "user", "sessionId": session, "cwd": cwd,
                "message": {"role": "user", "content": prompt}},
               {"type": "assistant", "sessionId": session, "cwd": cwd, "message": message}]
else:
    assert args[:4] == ["run", "--standalone", "--format", "json"]
    emit({"type": "step_start", "sessionID": session})
    native_parts = [{"id": "part-1", "type": "tool", "tool": "bash", "callID": "call-1",
                     "state": {"status": "error", "input": {"command": "false"}, "error": "failed"}}, text]
    if mode != "response-only":
        emit({"type": "tool_use", "sessionID": session, "part": native_parts[0]})
    emit({"type": "text", "sessionID": session, "part": text})
    data = {"info": {"id": session, "directory": cwd}, "messages": [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": prompt}]},
        {"info": {"role": "assistant", "time": {"completed": 1234},
                  "finish": "tool-calls" if mode == "failure" else "stop"},
         "parts": native_parts if mode != "response-only" else [text]}]}
    if mode == "v2":
        data = {"info": {"id": session, "location": {"directory": cwd}, "outcome": "succeeded"},
                "messages": [{"type": "user", "text": prompt},
                             {"type": "assistant", "time": {"completed": 1234}, "finish": "stop",
                              "content": [{"type": "tool", "name": "bash", "id": "call-1",
                                           "state": {"status": "error", "input": {"command": "false"},
                                                     "content": [], "error": {"message": "failed"}}}, text]},
                             {"type": "idle", "outcome": "succeeded"}]}
    Path("export.json").write_text(json.dumps(data))
    records = []
if records and mode != "missing":
    (root / f"{session}.jsonl").write_text("\\n".join(map(json.dumps, records)) + "\\n")
if mode == "malformed":
    print("not json")
if mode == "timeout":
    time.sleep(30)
"""
    )
    fake.chmod(0o755)
    output = tmp_path / "output"
    return output, [
        "--cwd",
        str(project),
        "--bin",
        str(fake),
        "--sessions-dir",
        str(native),
        "--output",
        str(output),
        "--model",
        "test/model",
    ]


@pytest.mark.parametrize("harness", ["pi", "claude", "opencode"])
def test_capture_and_search(prepared, harness):
    output, args = prepared
    assert collector.main(["--harness", harness, *args, "Inspect files", "Inspect files"]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert len(summary["runs"]) == 2
    with SessionStore(output / "index.sqlite") as store:
        sessions = store.list_sessions(SearchFilters(harnesses=[harness]))
        assert len(sessions) == 2
        events = list(store.iter_events(SearchFilters(kinds=["tool_call"])))
        assert len(events) == 2
        assert all(e.tool_name == "bash" and e.arguments == {"command": "false"} for e in events)
        for event in store.iter_events(SearchFilters()):
            assert store.read_evidence(event.id) == event.raw
        if harness == "opencode":
            failures = list(store.iter_events(SearchFilters(statuses=["failure"])))
            assert len(failures) == 2
            assert all(e.text == "failed" and e.call_id == "call-1" for e in failures)
    for run in summary["runs"]:
        assert run["harness"] == harness
        assert Path(run["transcript"]).is_file()
        assert (Path(run["run_dir"]) / "response.md").read_text() == "Done"


@pytest.mark.parametrize("harness", ["pi", "claude", "opencode"])
@pytest.mark.parametrize("mode", ["failure", "wrong-cwd", "malformed", "response-only", "timeout"])
def test_failed_run_stops_batch(prepared, monkeypatch, harness, mode):
    output, args = prepared
    monkeypatch.setenv("FAKE_MODE", mode)
    timeout = ["--timeout", "0.2"] if mode == "timeout" else []
    assert collector.main(["--harness", harness, *args, *timeout, "Inspect files", "Inspect files"]) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert len(summary["runs"]) == 1
    assert summary["not_started"] == ["Inspect files"]


def test_export_error_is_recorded(prepared, monkeypatch):
    output, args = prepared
    monkeypatch.setenv("FAKE_MODE", "export-failure")
    assert collector.main(["--harness", "opencode", *args, "Inspect files"]) == 1
    run = json.loads((output / "summary.json").read_text())["runs"][0]
    assert "capture_error" in run
    assert (Path(run["run_dir"]) / "manifest.json").is_file()


def test_auth_error_is_shown_even_with_success_subtype(prepared, monkeypatch, capsys):
    output, args = prepared
    monkeypatch.setenv("FAKE_MODE", "auth")
    assert collector.main(["--harness", "claude", *args, "Inspect files"]) == 1
    error = "Agent error: Failed to authenticate. API Error: 401 OAuth access token is invalid."
    assert error in capsys.readouterr().err
    run = json.loads((output / "summary.json").read_text())["runs"][0]
    assert run["turn_failed"] and not run["turn_completed"]


def test_opencode_v2_capture(prepared, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "v2")
    test_capture_and_search(prepared, "opencode")


def test_opencode_requires_explicit_model(prepared, capsys):
    output, args = prepared
    with pytest.raises(SystemExit):
        collector.main(["--harness", "opencode", *args[:-2], "Inspect files"])
    assert "requires --model" in capsys.readouterr().err
    assert not output.exists()


def test_unicode_line_separator_is_not_jsonl_boundary(tmp_path):
    events = [
        {"type": "session", "id": "one"},
        {
            "type": "message_end",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "first\u2028second"}]},
        },
        {"type": "agent_settled"},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events))
    result = inspect_output("pi", tmp_path)
    assert result["turn_completed"] and not result["malformed_stream"]
    assert result["assistant_messages"] == 1
