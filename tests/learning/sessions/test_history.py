"""Provider-shaped fixtures test evidence, recovery, and search semantics."""

import json

import pytest
from click.testing import CliRunner

from thearc.cli import main
from thearc.learning import SearchFilters, SearchQuery, SessionStore, SourceConfig


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


@pytest.fixture
def archive(tmp_path):
    pi = tmp_path / "pi"
    write(
        pi / "session.jsonl",
        [
            {"type": "session", "version": 3, "id": "same-id", "cwd": "/repo"},
            {
                "type": "message",
                "id": "u",
                "parentId": None,
                "message": {"role": "user", "content": "Please fix permission denied: EACCES"},
            },
            {
                "type": "message",
                "id": "a",
                "parentId": "u",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Checking access"},
                        {"type": "toolCall", "id": "call", "name": "bash", "arguments": {"command": "ls /root"}},
                    ],
                },
            },
            {
                "type": "message",
                "id": "r",
                "parentId": "a",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call",
                    "toolName": "bash",
                    "isError": True,
                    "content": [{"type": "text", "text": "permission denied: EACCES"}],
                },
            },
            {"type": "compaction", "id": "c", "parentId": "r", "summary": "permission denied summary"},
            {
                "type": "message",
                "id": "s",
                "parentId": "c",
                "message": {"role": "system", "content": "permission denied internal instruction"},
            },
        ],
    )
    claude = tmp_path / "claude"
    write(
        claude / "project" / "same-id.jsonl",
        [
            {
                "type": "user",
                "uuid": "u",
                "sessionId": "same-id",
                "cwd": "/repo",
                "message": {"role": "user", "content": "Fix permission denied"},
            },
            {
                "type": "assistant",
                "uuid": "a",
                "sessionId": "same-id",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "task", "name": "Agent", "input": {"prompt": "Investigate access"}}
                    ],
                },
            },
        ],
    )
    write(
        claude / "project" / "same-id" / "subagents" / "agent-child.jsonl",
        [
            {
                "type": "assistant",
                "sessionId": "same-id",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "read", "name": "Read", "input": {"file_path": "/repo/a.py"}}
                    ],
                },
            },
            {
                "type": "user",
                "sessionId": "same-id",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "read",
                            "is_error": True,
                            "content": "permission denied: EACCES",
                        }
                    ],
                },
            },
        ],
    )
    codex = tmp_path / "codex"
    write(
        codex / "sessions" / "rollout-test.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "same-id", "cwd": "/repo"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix EACCES"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call",
                    "name": "exec_command",
                    "arguments": '{"cmd":"ls /root"}',
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "call", "output": "permission denied: EACCES"},
            },
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Fix EACCES"}},
        ],
    )
    service = SessionStore(tmp_path / "index.sqlite")
    for name, root in [("pi", pi), ("claude", claude), ("codex", codex)]:
        service.register_source(SourceConfig(id=name, harness=name, root=root))
    service.sync()
    yield service
    service.close()


def test_common_search_preserves_provider_evidence(archive):
    page = archive.search(SearchQuery(text="permission denied: EACCES", filters=SearchFilters(kinds=["tool_result"])))
    assert {hit.event.harness for hit in page.hits} == {"pi", "codex", "claude"}
    assert len({hit.event.session_id for hit in page.hits}) == 3
    for hit in page.hits:
        assert hit.match_ranges == [(0, 25)]
        ref = hit.event.reference
        with ref.path.open("rb") as stream:
            stream.seek(ref.byte_offset)
            assert json.loads(stream.read(ref.byte_length)) == hit.event.raw
        assert hit.source_available


def test_call_result_join_and_delegation(archive):
    shell = archive.search(
        SearchQuery(text="EACCES", filters=SearchFilters(kinds=["tool_result"], action_kinds=["shell.exec"]))
    )
    assert {hit.event.harness for hit in shell.hits} == {"pi", "codex"}
    codex = next(hit.event for hit in shell.hits if hit.event.harness == "codex")
    assert codex.tool_name == "exec_command"
    assert codex.status is None  # Output text alone does not prove an exit code.
    call = archive.search(SearchQuery(text="Investigate access")).hits[0].event
    trace = archive.get_delegation_trace(call.run_id)
    assert len(trace.children) == 1
    assert trace.delegation_events[0].action_kind == "agent.spawn"
    assert archive.get_delegation_trace(trace.children[0]).parent_run_id == call.run_id


def test_session_and_run_projections_are_provider_neutral(archive):
    sessions = archive.list_sessions()
    assert {session.harness for session in sessions} == {"pi", "claude", "codex"}
    assert {session.native_id for session in sessions} == {"same-id"}
    for session in sessions:
        assert session.event_count > 0
        assert session.run_count >= 1
        assert archive.get_session(session.id) == session
        runs = archive.list_runs(session.id)
        assert len(runs) == session.run_count
        assert sum(run.event_count for run in runs) == session.event_count
        assert archive.session_stats(session.id)["event_count"] == session.event_count


def test_internal_records_and_duplicate_codex_events(archive):
    assert len(archive.search(SearchQuery(text="Fix EACCES")).hits) == 1
    assert not archive.search(SearchQuery(text="internal instruction")).hits
    assert len(archive.search(SearchQuery(text="internal instruction", include_internal=True)).hits) == 1
    assert not archive.search(SearchQuery(text="summary")).hits


def test_lexical_search_pagination_and_injection(archive):
    first = archive.search(SearchQuery(text="permission denied", mode="lexical", limit=2))
    assert first.has_more
    second = archive.search(SearchQuery(text="permission denied", mode="lexical", limit=2, offset=first.next_offset))
    assert not {h.event.id for h in first.hits} & {h.event.id for h in second.hits}
    assert not archive.search(SearchQuery(text="' OR 1=1 --")).hits
    assert not archive.search(SearchQuery(text='" OR "', mode="lexical")).hits


def test_incremental_reopen_partial_and_malformed(tmp_path):
    root = tmp_path / "pi"
    path = write(root / "s.jsonl", [{"type": "session", "id": "s"}])
    index = tmp_path / "index.sqlite"
    with SessionStore(index) as service:
        service.register_source(SourceConfig(id="pi", harness="pi", root=root))
        assert service.sync().events_added == 1
        assert service.sync().events_added == 0
        with path.open("ab") as stream:
            stream.write(b'{"type":"message","message":{"role":"user","content":"new phrase"}}')
        assert service.sync().partial_files == 1
        assert not service.search(SearchQuery(text="new phrase")).hits
    with SessionStore(index) as service:
        with path.open("ab") as stream:
            stream.write(b"\nnot-json\n")
        report = service.sync()
        assert report.events_added == 1
        assert report.records_skipped == 1
        assert len(service.search(SearchQuery(text="new phrase")).hits) == 1
        assert service.search(SearchQuery(text="new phrase")).warnings
        assert service.sync().records_skipped == 0
        assert len(service.diagnostics()) == 1


def test_rewrite_and_unavailable_source(tmp_path):
    root = tmp_path / "pi"
    path = write(root / "s.jsonl", [{"type": "message", "message": {"role": "user", "content": "old"}}])
    with SessionStore(tmp_path / "i.sqlite") as service:
        service.register_source(SourceConfig(id="pi", harness="pi", root=root))
        service.sync()
        old_id = service.search(SearchQuery(text="old")).hits[0].event.id
        write(path, [{"type": "message", "message": {"role": "user", "content": "new"}}])
        service.sync()
        assert not service.search(SearchQuery(text="old")).hits
        hit = service.search(SearchQuery(text="new")).hits[0]
        assert hit.event.id != old_id
        assert hit.event.reference.generation == 1
        path.rename(path.with_suffix(".removed"))
        service.sync()
        page = service.search(SearchQuery(text="new"))
        assert not page.hits[0].source_available
        assert page.warnings


def test_pi_context_excludes_sibling_branch(tmp_path):
    root = tmp_path / "pi"
    write(
        root / "s.jsonl",
        [
            {"type": "session", "id": "s"},
            *[
                {"type": "message", "id": name, "parentId": parent, "message": {"role": "user", "content": name}}
                for name, parent in [("root", None), ("abandoned", "root"), ("selected", "root")]
            ],
        ],
    )
    with SessionStore(tmp_path / "i.sqlite") as service:
        service.register_source(SourceConfig(id="pi", harness="pi", root=root))
        service.sync()
        event = service.search(SearchQuery(text="selected")).hits[0].event
        assert [item.text for item in service.get_context(event.id)] == ["root", "selected"]


def test_unicode_casefold_ranges(archive):
    assert archive._literal_ranges("Straße", "STRASSE", False) == [(0, 6)]


def test_failed_artifact_transaction_does_not_advance_checkpoint(tmp_path, monkeypatch):
    root = tmp_path / "pi"
    path = write(root / "s.jsonl", [{"type": "session", "id": "s"}])
    with SessionStore(tmp_path / "i.sqlite") as service:
        service.register_source(SourceConfig(id="pi", harness="pi", root=root))
        service.sync()
        with path.open("a") as stream:
            stream.write(json.dumps({"type": "message", "message": {"role": "user", "content": "recover"}}) + "\n")
        original = service._insert

        def failed_insert(event, subrecord):
            original(event, subrecord)
            raise RuntimeError("simulated crash after insert")

        monkeypatch.setattr(service, "_insert", failed_insert)
        with pytest.raises(RuntimeError, match="simulated crash"):
            service.sync()
        assert not service.search(SearchQuery(text="recover")).hits
        monkeypatch.setattr(service, "_insert", original)
        assert service.sync().events_added == 1
        assert len(service.search(SearchQuery(text="recover")).hits) == 1


def test_codex_child_lineage_and_stale_evidence(tmp_path):
    root = tmp_path / "codex"
    write(
        root / "rollout-parent.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "parent"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "parent phrase"}],
                },
            },
        ],
    )
    child = write(
        root / "rollout-child.jsonl",
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child",
                    "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent", "depth": 1}}},
                },
            },
        ],
    )
    with SessionStore(tmp_path / "i.sqlite") as service:
        service.register_source(SourceConfig(id="codex", harness="codex", root=root))
        service.sync()
        parent_event = service.search(SearchQuery(text="parent phrase")).hits[0].event
        trace = service.get_delegation_trace(parent_event.run_id)
        assert len(trace.children) == len(trace.descendants) == 1
        assert service.read_evidence(parent_event.id) == parent_event.raw
        write(child, [{"type": "session_meta", "payload": {"id": "other-child"}}])
        child_event = next(e for e in service.iter_events() if e.run_id == trace.children[0])
        with pytest.raises(ValueError, match="changed"):
            service.read_evidence(child_event.id)


def test_arrow_scan_and_snapshot(archive, tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow.dataset as ds

    batches = list(
        archive.scan_events(
            SearchFilters(kinds=["tool_result"]), columns=["id", "action_kind", "byte_offset"], batch_size=2
        )
    )
    assert [batch.num_rows for batch in batches] == [2, 1]
    assert batches[0].schema.names == ["id", "action_kind", "byte_offset"]
    destination = archive.export_dataset(tmp_path / "snapshot")
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["index_revision"] == archive.revision
    paths = [destination / name for name in manifest["files"]]
    assert ds.dataset(paths, format="parquet").count_rows() == len(list(archive.iter_events()))
    with pytest.raises(FileExistsError):
        archive.export_dataset(destination)


def test_cli_round_trip(tmp_path):
    root = tmp_path / "pi"
    write(root / "s.jsonl", [{"type": "message", "message": {"role": "user", "content": "find me"}}])
    runner = CliRunner()
    prefix = ["history", "--index", str(tmp_path / "cli.sqlite")]
    result = runner.invoke(main, [*prefix, "index", "--source", "pi", "--root", str(root)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, [*prefix, "search", "find me"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["hits"][0]["event"]["text"] == "find me"


def test_dataclaw_import_preserves_evidence_and_missing_results(tmp_path):
    root = tmp_path / "exports"
    write(
        root / "conversations.jsonl",
        [
            {
                "session_id": "first",
                "messages": [
                    {"role": "user", "content": "fix access"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_uses": [
                            {"tool": "Bash", "input": {"command": "ls /root"}},
                            {
                                "tool": "Read",
                                "input": {"path": "a.py"},
                                "output": {"text": "permission denied"},
                                "status": "failure",
                            },
                        ],
                    },
                ],
            },
            {"session_id": "second", "messages": [{"role": "user", "content": "another task"}]},
        ],
    )
    with SessionStore(tmp_path / "i.sqlite") as service:
        service.register_source(SourceConfig(id="export", harness="claude", root=root, format="dataclaw"))
        report = service.sync()
        assert report.records_skipped == 0
        events = list(service.iter_events())
        assert len(events) == 5
        assert len({event.session_id for event in events}) == 2
        calls = [event for event in events if event.kind == "tool_call"]
        assert len(calls) == 2
        assert all("messages" not in event.raw for event in events)
        for event in events:
            assert service.read_evidence(event.id) == event.raw
        result = (
            service.search(
                SearchQuery(
                    text="permission denied", filters=SearchFilters(action_kinds=["file.read"], statuses=["failure"])
                )
            )
            .hits[0]
            .event
        )
        assert result.kind == "tool_result"
        assert result.call_id == next(call.call_id for call in calls if call.tool_name == "Read")
        assert service.sync().events_added == 0


def test_antigravity_adapter(tmp_path):
    root = tmp_path / "antigravity" / "conv-1" / ".system_generated" / "logs"
    write(
        root / "transcript.jsonl",
        [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-09-26T15:00:00Z",
                "content": "Check route performance in FastAPI",
            },
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "created_at": "2026-09-26T15:00:05Z",
                "thinking": "Analyzing routes",
                "content": "Running benchmark test",
                "tool_calls": [
                    {
                        "name": "run_command",
                        "args": {"CommandLine": "pytest tests/benchmarks"},
                    }
                ],
            },
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "GENERIC",
                "status": "DONE",
                "created_at": "2026-09-26T15:00:10Z",
                "content": "Benchmark passed in 0.12s",
            },
        ],
    )
    with SessionStore(tmp_path / "test.sqlite") as service:
        service.register_source(SourceConfig(id="test-agy", harness="antigravity", root=tmp_path / "antigravity"))
        report = service.sync()
        assert report.records_skipped == 0
        events = list(service.iter_events())
        assert len(events) >= 4  # user message, thinking, assistant message, tool_call, tool_result
        tool_call = next(e for e in events if e.kind == "tool_call")
        assert tool_call.tool_name == "run_command"
        assert tool_call.action_kind == "shell.exec"
        tool_result = next(e for e in events if e.kind == "tool_result")
        assert tool_result.status == "success"
        assert "Benchmark passed" in tool_result.text

