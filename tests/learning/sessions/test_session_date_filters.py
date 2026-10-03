"""Session-level time selection, indexed normalization, and atomic evidence."""

import json
from datetime import UTC, datetime

import pytest

from thearc.learning import EvidenceSnapshot, SearchFilters, SearchQuery, SessionStore, SourceConfig

CUTOFF = datetime(2026, 9, 1, tzinfo=UTC)


def write_session(root, name, timestamps):
    records = [{"type": "session_meta", "payload": {"id": name}}]
    records.extend({
        "type": "response_item", "timestamp": timestamp,
        "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": f"evidence {name}"}]},
    } for timestamp in timestamps)
    (root / f"rollout-{name}.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")


@pytest.fixture
def dated_store(tmp_path):
    root = tmp_path / "logs"
    root.mkdir()
    for name, timestamps in {
        "old": ["2026-08-01T00:00:00Z", "2026-09-02T00:00:00Z"],
        "boundary": ["2026-09-01T00:00:00Z"],
        "offset-before": ["2026-09-01T01:00:00+02:00"],
        "offset-after": ["2026-08-31T23:00:00-02:00"],
        "micro-before": ["2026-08-31T23:59:59.999999Z"],
        "missing": [None],
        "invalid": ["bad-time"],
        "naive": ["2026-08-01T00:00:00"],
        "mixed": ["invalid", "2026-09-01T01:00:00+02:00", "2026-08-31T23:30:00Z"],
    }.items():
        write_session(root, name, timestamps)
    with SessionStore(tmp_path / "index.sqlite") as store:
        store.ingest(SourceConfig(id="experiments", harness="codex", root=root))
        yield store


def names(sessions):
    return {session.native_id for session in sessions}


def test_date_filters_use_utc_and_compose_in_sql(dated_store, monkeypatch):
    store = dated_store
    before = SearchFilters(started_before=CUTOFF, source_ids=["experiments"], harnesses=["codex"])
    expected = {"old", "offset-before", "micro-before", "mixed"}
    seen = []
    original = store._session_from_row

    def decode(row):
        seen.append(row["id"])
        return original(row)

    monkeypatch.setattr(store, "_session_from_row", decode)
    assert names(store.iter_sessions(before)) == expected
    assert len(seen) == len(expected)  # Rejected rows never become Python Session objects.
    assert names(store.list_sessions(before, limit=2) + store.list_sessions(before, limit=2, offset=2)) == expected
    assert names(store.iter_sessions(SearchFilters(started_after=CUTOFF))) == {"offset-after"}
    assert list(store.iter_sessions(before.model_copy(update={"source_ids": ["other"]}))) == []
    window = SearchFilters(started_after="2026-08-01T00:00:00Z", started_before="2026-09-01T02:00:00+02:00")
    assert names(store.iter_sessions(window)) == expected - {"old"}
    selected_id = next(store.iter_sessions(before)).id
    scoped = before.model_copy(update={"session_ids": [selected_id]})
    assert [s.id for s in store.iter_sessions(scoped)] == [selected_id]


def test_event_queries_filter_session_start_not_event_time(dated_store):
    filters = SearchFilters(started_before=CUTOFF)
    expected = {s.id for s in dated_store.iter_sessions(filters)}
    events = list(dated_store.iter_events(filters))
    assert {e.session_id for e in events} == expected
    assert any(e.timestamp == "2026-09-02T00:00:00Z" for e in events)
    for mode in ("literal", "lexical"):
        hits = dated_store.search(SearchQuery(text="evidence", mode=mode, filters=filters)).hits
        assert {h.event.session_id for h in hits} == expected


@pytest.mark.parametrize("arguments", [
    {"started_before": datetime(2026, 9, 1)},  # noqa: DTZ001 -- naive bounds must be rejected
    {"started_after": "2026-09-01"},
    {"started_before": "not-a-date"},
    {"started_before": CUTOFF, "started_after": CUTOFF},
    {"started_before": CUTOFF, "started_after": "2026-09-02T00:00:00Z"},
])
def test_invalid_filter_bounds_are_rejected(arguments):
    with pytest.raises(ValueError):
        SearchFilters(**arguments)


def test_filtered_snapshot_roundtrip_and_selector_validation(dated_store, tmp_path):
    filters = SearchFilters(started_before=CUTOFF)
    explicit = dated_store.snapshot(session_ids=[s.id for s in dated_store.iter_sessions(filters)])
    evidence = dated_store.snapshot(filters=filters)
    assert evidence.sha256 == explicit.sha256
    assert evidence.manifest["session_count"] == 4
    evidence.save(tmp_path / "evidence")
    assert EvidenceSnapshot.load(tmp_path / "evidence").sha256 == evidence.sha256
    assert dated_store.snapshot(filters=SearchFilters()).manifest["session_count"] == 9
    for kwargs, message in [
        ({}, "exactly one"),
        ({"session_ids": [], "filters": filters}, "exactly one"),
        ({"session_ids": []}, "Select sessions"),
        ({"filters": SearchFilters(started_before="1900-01-01T00:00:00Z")}, "No sessions match"),
        ({"filters": SearchFilters(roles=["user"])}, "unsupported event filters"),
    ]:
        with pytest.raises(ValueError, match=message):
            dated_store.snapshot(**kwargs)
        assert not dated_store.connection.in_transaction


def test_migration_rebuilds_utc_bounds_without_source_logs(dated_store, tmp_path):
    store = dated_store
    original_events = [e.model_dump_json() for e in store.iter_events()]
    # Emulate a pre-normalization index with incorrect lexical MIN/MAX projections.
    with store.connection:
        store.connection.execute("DELETE FROM settings WHERE key='projection_timestamps_utc'")
        store.connection.execute("UPDATE sessions SET started_at='invalid', ended_at='invalid'")
        store.connection.execute("UPDATE runs SET started_at='invalid', ended_at='invalid'")
    store.list_sources()[0].root.rename(tmp_path / "unavailable-logs")
    revision = store.revision
    with SessionStore(tmp_path / "index.sqlite") as migrated:
        assert migrated.revision == revision + 1
        mixed = next(s for s in migrated.iter_sessions() if s.native_id == "mixed")
        assert mixed.started_at == "2026-08-31T23:00:00.000000+00:00"
        assert mixed.ended_at == "2026-08-31T23:30:00.000000+00:00"
        assert migrated.list_runs(mixed.id)[0].started_at == mixed.started_at
        assert [e.model_dump_json() for e in migrated.iter_events()] == original_events
        assert len(list(migrated.iter_sessions(SearchFilters(started_before=CUTOFF)))) == 4
        assert names(s for s in migrated.iter_sessions() if s.started_at is None) == {"missing", "invalid", "naive"}
    with SessionStore(tmp_path / "index.sqlite") as reopened:
        assert reopened.revision == revision + 1  # Migration runs once.


def test_filter_selection_and_capture_share_one_revision(dated_store, tmp_path, monkeypatch):
    before = dated_store.snapshot(filters=SearchFilters())
    original = dated_store.iter_sessions
    with SessionStore(tmp_path / "index.sqlite") as writer:
        def select(filters):
            write_session(writer.list_sources()[0].root, "concurrent", ["2026-08-01T00:00:00Z"])
            writer.sync()
            return original(filters)

        monkeypatch.setattr(dated_store, "iter_sessions", select)
        during = dated_store.snapshot(filters=SearchFilters())
    assert during.sha256 == before.sha256
    assert during.manifest["source_revision"] == before.manifest["source_revision"]
    assert not dated_store.connection.in_transaction
    assert "concurrent" in names(original())


def test_filtered_snapshot_has_no_session_page_limit(tmp_path):
    root = tmp_path / "logs"
    root.mkdir()
    for number in range(1005):
        write_session(root, str(number), ["2026-08-01T00:00:00Z"])
    with SessionStore(tmp_path / "index.sqlite") as store:
        store.ingest(SourceConfig(id="large", harness="codex", root=root))
        evidence = store.snapshot(filters=SearchFilters(started_before=CUTOFF))
    assert evidence.manifest["session_count"] == 1005
    assert evidence.manifest["event_count"] == 1005


def test_date_filtered_export_manifest_is_json_serializable(dated_store, tmp_path):
    pytest.importorskip("pyarrow")
    filters = SearchFilters(started_before=CUTOFF)
    destination = dated_store.export_dataset(tmp_path / "export", filters)
    manifest = json.loads((destination / "manifest.json").read_text())
    assert SearchFilters.model_validate(manifest["filters"]) == filters
