import pytest

from thearcv2.sessions import Event, Session, SourceReference


def event(number, *, kind="message", role="assistant", text="", **kwargs):
    return Event(id=f"e{number}", source_id="fixture", harness="claude", session_id="s", run_id="r",
                 kind=kind, role=role, text=text,
                 reference=SourceReference(path="/fixture.jsonl", generation=0, byte_offset=number * 100,
                                           byte_length=100, line=number + 1), **kwargs)


@pytest.fixture
def history():
    return Session.from_events([
        event(0, role="user", text="Run tests"),
        event(1, kind="tool_call", tool_name="shell", call_id="c", arguments={"command": ["pytest", "--bad"]}),
        event(2, kind="tool_result", role="tool", call_id="c", status="failure", text="SyntaxError"),
        event(3, text="Retry fixed inputs"),
        event(4, role="user", text="Summarize"),
        event(5, text="Tests failed"),
    ])
