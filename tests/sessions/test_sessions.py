import json

import pytest

from thearc.sessions import Sessions, detect_harness


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def claude_session(native, text="Run the tests", when="2026-10-01T10:00:00Z"):
    return [
        {"type": "user", "sessionId": native, "timestamp": when, "message": {"role": "user", "content": text}},
        {"type": "attachment", "sessionId": native, "attachment": {"type": "model", "text": "native state"}},
        {"type": "assistant", "sessionId": native, "message": {"role": "assistant", "content": "Done"}},
    ]


@pytest.fixture
def claude_dir(tmp_path):
    root = tmp_path / "claude"
    write_jsonl(root / "project" / "11111111-aaaa.jsonl", claude_session("11111111-aaaa"))
    write_jsonl(
        root / "project" / "22222222-bbbb.jsonl", claude_session("22222222-bbbb", "Second", "2026-10-03T10:00:00Z")
    )
    return root


@pytest.fixture
def codex_dir(tmp_path):
    root = tmp_path / "codex"
    write_jsonl(
        root / "2026" / "rollout-x.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-10-02T10:00:00Z",
                "payload": {"id": "codex-thread", "cwd": "/w"},
            },
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Fix it"}]},
            },
        ],
    )
    return root


@pytest.fixture
def batch(tmp_path):
    root = tmp_path / "batch"
    write_jsonl(root / "sessions" / "run-1.jsonl", claude_session("33333333-cccc-dddd"))
    run = root / "runs" / "33333333ccccdddd"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps({"harness": "claude", "thread_id": "33333333-cccc-dddd", "status": "completed"})
    )
    return root


def test_detects_each_harness_and_refuses_ambiguity(tmp_path, claude_dir, codex_dir):
    assert detect_harness(claude_dir) == "claude"
    assert detect_harness(codex_dir) == "codex"
    write_jsonl(tmp_path / "opencode" / "s.jsonl", [{"info": {"id": "s"}, "messages": []}])
    assert detect_harness(tmp_path / "opencode") == "opencode"
    with pytest.raises(ValueError, match="claude, codex"):
        detect_harness(tmp_path)


def test_collects_multiple_harnesses_into_one_index(tmp_path, claude_dir, codex_dir, batch):
    with Sessions(tmp_path / "index.sqlite") as sessions:
        assert sessions.add(claude_dir).harness == "claude"
        assert sessions.add(codex_dir).harness == "codex"
        source = sessions.add(batch)
        assert source.root == (batch / "sessions").resolve()
        assert sessions.add(claude_dir) == sessions.sources()[1]  # re-adding reuses the registration
        assert len(sessions.sources()) == 3
        assert {s.harness for s in sessions.list()} == {"claude", "codex"}
        assert [s.native_id for s in sessions.list(harness="claude", since="2026-10-02")] == ["22222222-bbbb"]
        assert sessions.get().metadata.native_id == "22222222-bbbb"  # most recent

        by_native = sessions.get("codex-thread")
        assert sessions.get(by_native.id[:10]).id == by_native.id
        assert sessions.get("1111").metadata.native_id == "11111111-aaaa"
        recorded = sessions.get("33333333ccccdddd")  # collector run ID
        assert recorded.metadata.native_id == "33333333-cccc-dddd"
        assert sessions.run(recorded)[1]["status"] == "completed"
        assert sessions.run(by_native) is None
        assert len(recorded.trajectories) == 1
        with pytest.raises(LookupError, match="matches no"):
            sessions.get("missing")


def test_index_must_stay_outside_source(claude_dir):
    with Sessions(claude_dir / "index.sqlite") as sessions, pytest.raises(ValueError, match="outside"):
        sessions.add(claude_dir)


def test_sessions_are_indexed_under_their_own_ids_and_copies_once(claude_dir, tmp_path):
    copy = tmp_path / "copy-of-claude"
    write_jsonl(copy / "project" / "11111111-aaaa.jsonl", claude_session("11111111-aaaa"))
    with Sessions(tmp_path / "index.sqlite") as sessions:
        first = sessions.add(claude_dir)
        second = sessions.add(copy, sync=False)
        report = sessions.sync(source_ids=[second.id])
        assert any("11111111-aaaa" in w and "already indexed" in w for w in report.warnings)
        ids = sorted(record.id for record in sessions.list())
        assert ids == ["11111111-aaaa", "22222222-bbbb"]  # The native IDs; the copy is not indexed twice.
        assert sessions.get("11111111-aaaa").metadata.source_id == first.id  # The first copy is kept.


def test_an_index_with_source_scoped_ids_is_rebuilt(claude_dir, tmp_path):
    import sqlite3

    path = tmp_path / "index.sqlite"
    with Sessions(path) as sessions:
        sessions.add(claude_dir)
    with sqlite3.connect(path) as connection:  # Pretend it was written before session IDs were native.
        connection.execute("UPDATE settings SET value=1 WHERE key='schema_version'")
        connection.execute("UPDATE events SET session_id='old-' || session_id")
    with Sessions(path) as sessions:
        assert sessions.list() == []  # Cleared on opening…
        sessions.sync()
        assert sorted(record.id for record in sessions.list()) == ["11111111-aaaa", "22222222-bbbb"]  # …and re-read.
