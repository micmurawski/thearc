"""Portable evidence and retrieval contracts, without SDK imports or inference."""

import json
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from threading import Event as CancellationEvent

import pytest
from click.testing import CliRunner
from thearc.learning.service import HistoryService as LegacyService

from thearc import MetaAgent, Skill
from thearc.cli import main
from thearc.learning import (
    EvidenceSnapshot,
    EvidenceToolError,
    HandoffMode,
    HistoryService,
    RetrievalLimits,
    SessionStore,
    SourceConfig,
    load_handoff,
    prepare_handoff,
    render_context,
    save_handoff,
    session_tools,
    write_files,
)


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    records = [
        {"type": "session_meta", "payload": {"id": "native", "cwd": "/private/original"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer",
         "content": [{"type": "input_text", "text": "DO NOT EXPORT THIS"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "Investigate graphify"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "c1", "name": "shell",
         "arguments": json.dumps({"command": "graphify query", "api_key": "secret-value"})}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
         "output": "# Guide (helpful: 7, neutral: 0, harmful: 2)\n" + "graph text αβ " * 1000}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "Finished graph inspection"}]}},
    ]
    (root / "rollout-native.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    with SessionStore(tmp_path / "index.sqlite") as service:
        service.ingest(SourceConfig(id="test", harness="codex", root=root))
        yield service


def snapshot(store, **kwargs):
    return store.snapshot(session_ids=[s.id for s in store.list_sessions()], **kwargs)


def test_compatibility_alias():
    assert SessionStore is HistoryService is LegacyService


def test_offline_handoff_roundtrip_and_no_replay(store, tmp_path):
    workspace = tmp_path / "worktree"
    workspace.mkdir()
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "-c", "user.name=Test", "-c", "user.email=test@example.org",
                    "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture"],
                   check=True, capture_output=True)
    evidence = snapshot(store)
    evidence_path = evidence.save(tmp_path / "cli-evidence")
    runner = CliRunner()
    for number, prefix in enumerate([["handoff"], ["sessions", "handoff"], ["history", "handoff"]]):
        output = tmp_path / f"cli-handoff-{number}"
        prepared = runner.invoke(main, [*prefix, "prepare", "--snapshot", str(evidence_path),
                                       "--task", "Review the evidence", "--workspace", str(workspace),
                                       "--output", str(output)])
        assert prepared.exit_code == 0, prepared.output
        assert not json.loads(prepared.output)["plan"]["permissions"]["execution_enabled"]
        shown = runner.invoke(main, [*prefix, "show", str(output)])
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["sha256"] == json.loads(prepared.output)["sha256"]
    plan = prepare_handoff(evidence, task="Investigate without executing historical commands", workspace=workspace)
    path = save_handoff(plan, tmp_path / "handoff")
    loaded = load_handoff(path)
    assert loaded.sha256 == plan.sha256
    assert loaded.preview() == plan.preview()
    assert loaded.data["workspace"]["clean"]
    assert not loaded.data["permissions"]["execution_enabled"]
    loaded.data["task"] = "changed"
    assert loaded.sha256 == plan.sha256
    assert not list(workspace.glob("*.json"))
    for mode in (HandoffMode.FORK, HandoffMode.RESUME, HandoffMode.IMPORT):
        with pytest.raises(ValueError, match="not enabled"):
            prepare_handoff(evidence, task="continue", workspace=workspace, mode=mode)
    with pytest.raises(FileExistsError):
        save_handoff(plan, path)
    saved = json.loads((path / "plan.json").read_text())
    saved["plan"]["task"] = "tampered"
    (path / "plan.json").write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_handoff(path)


def test_unicode_search_ranges_and_rejection_audit(store):
    # Lowercasing U+0130 expands to two codepoints; ranges must reference original text.
    event = store.list_sessions()[0]
    source = store.list_sources()[0].root / "rollout-native.jsonl"
    with source.open("a") as stream:
        stream.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": "İ" * 300 + "needle"}]}}) + "\n")
    store.sync()
    evidence = snapshot(store)
    tools = session_tools(evidence)
    result = tools.call("search_events", {"query": "needle"})["items"][0]
    original = tools.call("read_event", {"session_id": event.id, "event_id": result["event_id"],
                                         "max_chars": 8000})["content"]
    start, end = result["range"]
    assert result["snippet"] == original[start:end]
    assert "needle" in result["snippet"]
    with pytest.raises(EvidenceToolError):
        tools.call("shell", {"api_key": "secret"})
    assert tools.audit[-1]["error"] == "unknown_tool"
    assert "secret" not in json.dumps(tools.audit)


def test_snapshot_is_immutable_full_and_rank_free(store):
    evidence = snapshot(store)
    assert evidence.manifest["event_count"] == 4
    assert [e["kind"] for e in evidence.events] == ["message", "tool_call", "tool_result", "message"]
    assert len(evidence.events[2]["text"]) > 6000
    serialized = json.dumps(evidence.events)
    assert "secret-value" not in serialized and "helpful: 7" not in serialized
    assert "DO NOT EXPORT THIS" not in serialized and "raw" not in evidence.events[0]
    copy = evidence.events
    copy[1]["arguments"]["command"] = "changed"
    evidence.manifest["policy"]["redact"] = False
    evidence.sessions[0]["native_id"] = "changed"
    assert evidence.events[1]["arguments"]["command"] == "graphify query"
    assert evidence.sessions[0]["native_id"] == "native"
    assert evidence.manifest["policy"]["redact"] is True
    with pytest.raises(FrozenInstanceError):
        evidence._events = ()
    assert snapshot(store).sha256 == evidence.sha256


def test_snapshot_keeps_one_revision_during_concurrent_sync(store, tmp_path, monkeypatch):
    before = snapshot(store)
    source = store.list_sources()[0].root / "rollout-native.jsonl"
    original_get = store.get_session
    with SessionStore(tmp_path / "index.sqlite") as writer:
        def change_during_read(session_id):
            # snapshot_store has already read revision and source rows in its transaction.
            with source.open("a") as stream:
                stream.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                                         "content": [{"type": "input_text", "text": "Later generation"}]}}) + "\n")
            writer.sync()
            return original_get(session_id)

        monkeypatch.setattr(store, "get_session", change_during_read)
        during = snapshot(store)
        assert during.sha256 == before.sha256
        assert during.manifest["source_revision"] == before.manifest["source_revision"]
    monkeypatch.setattr(store, "get_session", original_get)
    after = snapshot(store)
    assert after.sha256 != before.sha256
    assert len(after.events) == len(before.events) + 1


def test_portability_after_source_changes_and_deletion(store, tmp_path):
    evidence = snapshot(store)
    path = evidence.save(tmp_path / "snapshot")
    source = store.list_sources()[0].root / "rollout-native.jsonl"
    source.write_text('{"type":"session_meta","payload":{"id":"replacement"}}\n')
    store.sync()
    source.unlink()
    shutil.move(path, tmp_path / "moved")
    loaded = EvidenceSnapshot.load(tmp_path / "moved")
    assert loaded.sha256 == evidence.sha256
    assert loaded.events == evidence.events
    assert not store.connection.in_transaction


def test_storage_integrity_and_limits(store, tmp_path):
    with pytest.raises(ValueError, match="storage limit"):
        snapshot(store, max_bytes=20)
    assert not store.connection.in_transaction
    path = snapshot(store).save(tmp_path / "snapshot")
    with pytest.raises(FileExistsError):
        snapshot(store).save(path)
    with pytest.raises(ValueError, match="storage limit"):
        EvidenceSnapshot.load(path, max_bytes=20)
    with (path / "records.jsonl").open("a") as stream:
        stream.write('{}\n')
    with pytest.raises(ValueError):
        EvidenceSnapshot.load(path)


def test_destination_guards(store, tmp_path):
    evidence = snapshot(store)
    with pytest.raises(ValueError, match="source roots"):
        evidence.save(store.list_sources()[0].root / "output")
    evidence.save(tmp_path / "safe")
    loaded = EvidenceSnapshot.load(tmp_path / "safe")
    with pytest.raises(ValueError, match="source roots"):
        loaded.save(store.list_sources()[0].root / "output")
    (tmp_path / "link").symlink_to(tmp_path / "source", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        loaded.save(tmp_path / "link" / "output")


def test_ingest_is_source_scoped(store, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "rollout-other.jsonl").write_text('{"type":"session_meta","payload":{"id":"other"}}\n')
    store.register_source(SourceConfig(id="other", harness="codex", root=other))
    assert store.ingest(store.list_sources()[1]).events_added == 0  # sorted IDs: other, test
    assert len(store.list_sessions()) == 1
    assert store.sync().events_added == 1


def test_rendering_does_not_mutate_or_split_tool_groups(store, tmp_path):
    evidence = snapshot(store)
    context = render_context(evidence, max_chars=4000)
    assert len(context.text) <= 4000
    pair = {e["id"] for e in evidence.events if e.get("call_id")}
    assert pair <= set(context.omitted_event_ids)
    assert pair.isdisjoint(context.included_event_ids)
    assert len(evidence.events[2]["text"]) > 6000
    path = write_files(evidence, tmp_path / "files", format="markdown")
    assert (path / "session-0001.md").is_file()
    assert not (path / "AGENTS.md").exists()
    assert EvidenceSnapshot.load(path).sha256 == evidence.sha256


def test_tools_scope_pagination_and_audit(store):
    evidence = snapshot(store)
    tools = session_tools(evidence)
    session_id = evidence.sessions[0]["id"]
    first = tools.call("search_events", {"query": "graph", "limit": 1})
    cursor = first["next_cursor"]
    assert cursor
    second = tools.call("search_events", {"query": "graph", "limit": 1, "cursor": cursor})
    assert first["items"][0]["event_id"] != second["items"][0]["event_id"]
    with pytest.raises(EvidenceToolError, match="Cursor"):
        tools.call("search_events", {"query": "other", "cursor": cursor})
    with pytest.raises(EvidenceToolError):
        session_tools(evidence).call("search_events", {"query": "graph", "cursor": cursor})
    event_id = evidence.events[2]["id"]
    with pytest.raises(EvidenceToolError):
        tools.call("read_event", {"session_id": "foreign", "event_id": event_id})
    page = tools.call("read_event", {"session_id": session_id, "event_id": event_id, "max_chars": 300})
    assert len(page["content"]) == 300 and page["next_offset"] == 300
    context = tools.call("read_context", {"session_id": session_id, "event_id": event_id, "before": 0, "after": 0})
    assert set(context["items"][0]["event_ids"]) == {e["id"] for e in evidence.events if e.get("call_id")}
    tools.audit[0]["result"].clear()
    assert tools.audit[0]["result"]
    assert all(s["inputSchema"]["additionalProperties"] is False for s in tools.schemas)


def test_tool_budgets_and_resource_snapshot(store):
    evidence = snapshot(store)
    with pytest.raises(ValueError):
        session_tools(snapshot(store, hide_ranks=False))
    tool = session_tools(evidence, limits=RetrievalLimits(max_calls=1))
    tool.call("list_sessions", {})
    with pytest.raises(EvidenceToolError) as exc:
        tool.call("list_sessions", {})
    assert exc.value.code == "budget_exhausted"
    with pytest.raises(EvidenceToolError):
        session_tools(evidence, limits=RetrievalLimits(max_total_chars=1)).call("list_sessions", {})
    cancelled = CancellationEvent()
    cancelled.set()
    with pytest.raises(EvidenceToolError) as exc:
        session_tools(evidence, cancelled=cancelled).call("list_sessions", {})
    assert exc.value.code == "cancelled"
    agent = MetaAgent(name="agent", skills=[Skill(name="graphify", instructions="# Query\nOriginal")])
    tool = session_tools(evidence, agent=agent)
    agent.skills["graphify"].instructions = "Modified"
    assert "Original" in tool.call("read_resource", {"kind": "skill", "name": "graphify"})["content"]
    with pytest.raises(EvidenceToolError):
        tool.call("read_resource", {"kind": "skill", "name": "unselected"})
    with pytest.raises(EvidenceToolError):
        tool.call("list_sessions", {"database": "other.sqlite"})


@pytest.mark.parametrize("group", ["evidence", "sessions", "history"])
def test_offline_cli(store, tmp_path, group):
    runner = CliRunner()
    session_id = store.list_sessions()[0].id
    path = tmp_path / "snapshot"
    result = runner.invoke(main, [group, "--index", str(tmp_path / "index.sqlite"), "snapshot",
                                 "--session-id", session_id, "--output", str(path)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, [group, "inspect", "--snapshot", str(path)])
    assert result.exit_code == 0, result.output
    assert session_id in result.output
    result = runner.invoke(main, [group, "export", "--snapshot", str(path), "--format", "context",
                                 "--output", str(tmp_path / "context")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "context" / "context.json").is_file()
