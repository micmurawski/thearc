import json

import pytest

from thearc.learning.sessions.adapters import CodexAdapter
from thearc.learning.sessions.models import SourceConfig
from thearc.resources import SessionStore


@pytest.mark.parametrize(
    "output,expected",
    [
        ({"exit_code": 0, "output": "Example: error"}, "success"),
        ('{"exit_code": 2, "output": "failed"}', "failure"),
        (
            [
                {"type": "input_text", "text": "Script completed"},
                {"type": "input_text", "text": '{"exit_code":1,"output":"bad query"}'},
            ],
            "failure",
        ),
        ([{"exit_code": 0}, {"exit_code": None}], None),
        ({"exit_code": False}, None),
        ({"output": '{"exit_code":1}'}, None),
        ("The docs show an error and exit_code=1", None),
    ],
)
def test_only_explicit_codex_exit_status_is_indexed(output, expected):
    assert CodexAdapter.result_status(output) == expected


def test_sync_reindexes_old_codex_parser_without_changing_captured_history(tmp_path, monkeypatch):
    source = tmp_path / "logs"
    source.mkdir()
    records = [
        {"type": "session_meta", "payload": {"id": "s"}},
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call",
                "output": '{"exit_code":1,"output":"bad query"}',
            },
        },
    ]
    path = source / "rollout-s.jsonl"
    text = "\n".join(map(json.dumps, records)) + "\n"
    path.write_text(text)
    with SessionStore(tmp_path / "index.sqlite") as store:
        with monkeypatch.context() as patch:
            patch.setattr(CodexAdapter, "version", "native-1")
            patch.setattr(CodexAdapter, "result_status", staticmethod(lambda _: None))
            store.ingest(SourceConfig(id="codex", harness="codex", root=source))
        old_session = next(iter(store.search(kind="tool_result"))).session
        assert old_session.events[-1].status is None
        store.sync()
        failure = next(iter(store.search(status="failure")))
        assert failure.event.reference.generation == 1
        assert old_session.events[-1].status is None
    assert path.read_text() == text
