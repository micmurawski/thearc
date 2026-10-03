"""Regression cases found while exercising public DataClaw exports."""

import json

from thearc.learning import SearchFilters, SearchQuery, SessionStore, SourceConfig
from thearc.learning.sessions.adapters import DataClawAdapter


def test_nested_output_exit_code_and_parser_upgrade(tmp_path, monkeypatch):
    root = tmp_path / "exports"
    root.mkdir()
    record = {
        "session_id": "s",
        "messages": [
            {
                "role": "assistant",
                "tool_uses": [
                    {
                        "tool": "exec_command",
                        "input": {"cmd": "pytest"},
                        "status": "success",
                        "output": {"output": "first line\npermission denied", "exit_code": 1},
                    },
                ],
            }
        ],
    }
    (root / "conversations.jsonl").write_text(json.dumps(record) + "\n")
    with SessionStore(tmp_path / "index.sqlite") as history:
        history.register_source(SourceConfig(id="codex", harness="codex", format="dataclaw", root=root))
        history.sync()
        hit = history.search(
            SearchQuery(
                text="first line\npermission denied", filters=SearchFilters(kinds=["tool_result"], statuses=["failure"])
            )
        ).hits[0]
        assert hit.event.text == "first line\npermission denied"
        assert hit.event.status == "failure"  # A recorded exit code overrides exporter status.
        assert history.read_evidence(hit.event.id) == record["messages"][0]["tool_uses"][0]
        old_id = hit.event.id
        assert history.sync().events_added == 0
        monkeypatch.setattr(DataClawAdapter, "version", "regression-upgrade")
        assert history.sync().events_added == 2
        assert history.search(SearchQuery(text="permission denied")).hits[0].event.id != old_id
        assert len(list(history.iter_events())) == 2
