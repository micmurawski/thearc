"""Tests for thearc.learning.sessions.translation module.

Run with:
    python -m pytest tests/learning/sessions/test_session_translation.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from thearc.learning.sessions.translation.builders import (
    AntigravityRecordBuilder,
    ClaudeRecordBuilder,
    CodexRecordBuilder,
    OpenCodeRecordBuilder,
    PiRecordBuilder,
)
from thearc.learning.sessions.translation.extractors import MessageExtractor
from thearc.learning.sessions.translation.models import (
    NativeSession,
    TextMessage,
    codex_date_parts,
    encode_pi_cwd,
    epoch_ms_to_iso,
    is_uuid,
    iso_to_epoch_ms,
    now_iso,
    opencode_id,
    opencode_slug,
    sanitize_claude_cwd,
)
from thearc.learning.sessions.translation.translator import (
    SessionTranslator,
    _normalize_provider,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TS = "2026-01-15T10:30:00.000Z"
_UUID = "11111111-2222-3333-4444-555555555555"
_CWD = "/home/user/project"


def _make_source(provider: str, records: list | None = None) -> NativeSession:
    return NativeSession(
        provider=provider,
        session_id=_UUID,
        cwd=_CWD,
        timestamp=_TS,
        path=Path("/tmp/fake.jsonl"),
        records=records or [],
    )


def _codex_records() -> list:
    return [
        {
            "type": "session_meta",
            "timestamp": _TS,
            "payload": {"id": _UUID, "cwd": _CWD, "timestamp": _TS},
        },
        {
            "type": "message",
            "timestamp": _TS,
            "payload": {"type": "message", "role": "user", "content": "What is 2+2?"},
        },
        {
            "type": "message",
            "timestamp": _TS,
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "It is 4."}],
                "model": "gpt-4o",
            },
        },
    ]


def _pi_records() -> list:
    return [
        {
            "type": "session_meta",
            "timestamp": _TS,
            "payload": {"id": _UUID, "cwd": _CWD, "timestamp": _TS},
        },
        {
            "type": "message",
            "timestamp": _TS,
            "message": {"role": "user", "content": "Hello Pi"},
        },
        {
            "type": "message",
            "timestamp": _TS,
            "message": {"role": "assistant", "content": "Hi there!", "model": "llama-3"},
        },
        {
            "type": "compaction",
            "timestamp": _TS,
            "summary": "Previous context summarised here.",
        },
    ]


def _claude_records() -> list:
    return [
        {
            "type": "user",
            "timestamp": _TS,
            "cwd": _CWD,
            "sessionId": _UUID,
            "message": {
                "id": "msg_abc",
                "role": "user",
                "content": [{"type": "text", "text": "Explain async/await"}],
                "type": "message",
            },
        },
        {
            "type": "assistant",
            "timestamp": _TS,
            "cwd": _CWD,
            "sessionId": _UUID,
            "message": {
                "id": "msg_xyz",
                "role": "assistant",
                "content": [{"type": "text", "text": "async/await makes asynchronous code readable."}],
                "model": "claude-sonnet-4",
                "type": "message",
            },
        },
        {
            "type": "system",
            "subtype": "compact_boundary",
            "timestamp": _TS,
            "content": "Summary of prior work.",
            "cwd": _CWD,
            "sessionId": _UUID,
        },
    ]


def _opencode_records() -> list:
    return [
        {
            "id": "ses_abc123",
            "title": "test-session",
            "cwd": _CWD,
            "createdAt": _TS,
            "messages": [
                {
                    "info": {"id": "msg_1", "role": "user", "time": iso_to_epoch_ms(_TS)},
                    "parts": [{"type": "text", "text": "OpenCode question"}],
                },
                {
                    "info": {"id": "msg_2", "role": "assistant", "time": iso_to_epoch_ms(_TS), "modelID": "gpt-4o"},
                    "parts": [{"type": "text", "text": "OpenCode answer"}],
                },
            ],
        }
    ]


def _agy_records() -> list:
    return [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": _TS,
            "content": "What does this repo do?",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": _TS,
            "content": [{"type": "text", "text": "It is a learning pipeline for agents."}],
        },
    ]


# ---------------------------------------------------------------------------
# Model / utility tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_is_uuid_valid(self):
        assert is_uuid(_UUID)

    def test_is_uuid_invalid(self):
        assert not is_uuid("ses_abc123")
        assert not is_uuid("")

    def test_epoch_round_trip(self):
        ms = iso_to_epoch_ms(_TS)
        assert ms > 0
        back = epoch_ms_to_iso(ms)
        assert back.endswith("Z")
        assert "2026-01-15" in back

    def test_encode_pi_cwd(self):
        encoded = encode_pi_cwd("/home/user/project")
        assert encoded.startswith("--")
        assert encoded.endswith("--")

    def test_sanitize_claude_cwd(self):
        sanitized = sanitize_claude_cwd("/home/user/project")
        assert "/" not in sanitized

    def test_codex_date_parts(self):
        year, month, day = codex_date_parts(_TS)
        assert year == "2026"
        assert month == "01"
        assert day == "15"

    def test_opencode_id_prefix(self):
        oid = opencode_id("ses", _TS)
        assert oid.startswith("ses_")

    def test_opencode_slug(self):
        slug = opencode_slug("Hello World, can you help me with this?")
        assert "-" in slug
        assert " " not in slug

    def test_now_iso(self):
        ts = now_iso()
        assert ts.endswith("Z")
        assert "T" in ts


# ---------------------------------------------------------------------------
# Extractor tests
# ---------------------------------------------------------------------------


class TestMessageExtractor:
    def setup_method(self):
        self.ex = MessageExtractor()

    def test_from_codex(self):
        source = _make_source("codex", _codex_records())
        msgs = self.ex.from_codex(source)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].text == "What is 2+2?"
        assert msgs[1].role == "assistant"
        assert msgs[1].text == "It is 4."
        assert msgs[1].model == "gpt-4o"

    def test_from_pi(self):
        source = _make_source("pi", _pi_records())
        msgs = self.ex.from_pi(source)
        assert len(msgs) == 3  # user + assistant + compaction
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"
        assert msgs[2].is_compaction is True
        assert msgs[2].text == "Previous context summarised here."

    def test_from_claude(self):
        source = _make_source("claude", _claude_records())
        msgs = self.ex.from_claude(source)
        assert len(msgs) == 3  # user + assistant + compaction
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"
        assert msgs[1].model == "claude-sonnet-4"
        assert msgs[2].is_compaction is True

    def test_from_opencode(self):
        source = _make_source("opencode", _opencode_records())
        msgs = self.ex.from_opencode(source)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].text == "OpenCode question"
        assert msgs[1].role == "assistant"
        assert msgs[1].model == "gpt-4o"

    def test_from_agy(self):
        source = _make_source("agy", _agy_records())
        msgs = self.ex.from_agy(source)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].text == "What does this repo do?"
        assert msgs[1].role == "assistant"
        assert "learning pipeline" in msgs[1].text

    def test_empty_session(self):
        for provider in ("codex", "pi", "claude", "agy"):
            source = _make_source(provider, [])
            method = getattr(self.ex, f"from_{provider}")
            assert method(source) == []

    def test_opencode_empty(self):
        source = _make_source("opencode", [])
        assert self.ex.from_opencode(source) == []


# ---------------------------------------------------------------------------
# Builder tests
# ---------------------------------------------------------------------------


def _sample_messages() -> list[TextMessage]:
    return [
        TextMessage("user", "Hello, can you help?", _TS),
        TextMessage("assistant", "Of course!", _TS, model="gpt-4o", provider="openai"),
        TextMessage("user", "Summary of context.", _TS, is_compaction=True),
    ]


class TestCodexRecordBuilder:
    def test_build_structure(self):
        msgs = _sample_messages()
        records = CodexRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        assert records[0]["type"] == "session_meta"
        assert records[0]["payload"]["id"] == _UUID
        assert records[0]["payload"]["cwd"] == _CWD
        # 2 regular messages + 1 compaction → task_complete
        types = [r["type"] for r in records[1:]]
        assert "message" in types
        assert "task_complete" in types

    def test_build_empty(self):
        records = CodexRecordBuilder().build(_UUID, _CWD, _TS, [])
        assert len(records) == 1  # only session_meta


class TestPiRecordBuilder:
    def test_build_structure(self):
        msgs = _sample_messages()
        records = PiRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        assert records[0]["type"] == "session_meta"
        types = [r["type"] for r in records[1:]]
        assert "message" in types
        assert "compaction" in types

    def test_compaction_record(self):
        msgs = [TextMessage("user", "Compacted history.", _TS, is_compaction=True)]
        records = PiRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        comp = next(r for r in records if r.get("type") == "compaction")
        assert comp["summary"] == "Compacted history."


class TestClaudeRecordBuilder:
    def test_build_structure(self):
        msgs = _sample_messages()
        records = ClaudeRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        # Should have user/assistant/system records (no session_meta in Claude format)
        types = [r.get("type") for r in records]
        assert "user" in types
        assert "assistant" in types
        assert "system" in types  # compaction

    def test_session_id_in_records(self):
        msgs = [TextMessage("user", "Hi", _TS)]
        records = ClaudeRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        assert records[0]["sessionId"] == _UUID

    def test_message_has_content_list(self):
        msgs = [TextMessage("user", "Hi", _TS)]
        records = ClaudeRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        content = records[0]["message"]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"


class TestOpenCodeRecordBuilder:
    def test_build_structure(self):
        msgs = _sample_messages()
        records = OpenCodeRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        assert len(records) == 1
        export = records[0]
        assert export["id"] == _UUID
        assert export["cwd"] == _CWD
        assert len(export["messages"]) == 3

    def test_message_parts(self):
        msgs = [TextMessage("user", "Hello", _TS)]
        records = OpenCodeRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        msg = records[0]["messages"][0]
        assert msg["parts"][0]["type"] == "text"
        assert msg["parts"][0]["text"] == "Hello"


class TestAntigravityRecordBuilder:
    def test_build_structure(self):
        msgs = _sample_messages()
        records = AntigravityRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        assert len(records) == 3
        assert records[0]["type"] == "USER_INPUT"
        assert records[1]["type"] == "PLANNER_RESPONSE"
        assert records[0]["source"] == "USER_EXPLICIT"
        assert records[1]["source"] == "MODEL"

    def test_step_indices(self):
        msgs = [TextMessage("user", "A", _TS), TextMessage("assistant", "B", _TS)]
        records = AntigravityRecordBuilder().build(_UUID, _CWD, _TS, msgs)
        assert records[0]["step_index"] == 0
        assert records[1]["step_index"] == 1


# ---------------------------------------------------------------------------
# Translator tests
# ---------------------------------------------------------------------------


class TestNormalizeProvider:
    @pytest.mark.parametrize("alias,expected", [
        ("agy", "agy"), ("antigravity", "agy"), ("gemini", "agy"),
        ("claude", "claude"), ("claudecode", "claude"), ("claude-code", "claude"),
        ("codex", "codex"),
        ("pi", "pi"), ("pi-agent", "pi"),
        ("opencode", "opencode"), ("open-code", "opencode"),
    ])
    def test_aliases(self, alias, expected):
        assert _normalize_provider(alias) == expected

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            _normalize_provider("unknown-agent")


class TestSessionTranslator:
    """Integration tests using in-memory fake stores."""

    def _make_translator_with_source(self, source: NativeSession) -> SessionTranslator:
        t = SessionTranslator()

        class FakeStore:
            provider_name = source.provider
            def load(self, session_id): return source
            def list(self): return []
            def load_path(self, path): return source

        store = FakeStore()
        setattr(t, f"_{source.provider}", store)
        return t

    @pytest.mark.parametrize("target", ["pi", "claude", "opencode", "agy"])
    def test_codex_to_all(self, target):
        source = _make_source("codex", _codex_records())
        t = self._make_translator_with_source(source)
        plan = t.plan("codex", target, session_id=_UUID)
        assert plan.source is source
        assert len(plan.records) > 0
        assert plan.destination is not None

    @pytest.mark.parametrize("target", ["codex", "claude", "opencode", "agy"])
    def test_pi_to_all(self, target):
        source = _make_source("pi", _pi_records())
        t = self._make_translator_with_source(source)
        plan = t.plan("pi", target, session_id=_UUID)
        assert len(plan.records) > 0

    @pytest.mark.parametrize("target", ["codex", "pi", "opencode", "agy"])
    def test_claude_to_all(self, target):
        source = _make_source("claude", _claude_records())
        t = self._make_translator_with_source(source)
        plan = t.plan("claude", target, session_id=_UUID)
        assert len(plan.records) > 0

    @pytest.mark.parametrize("target", ["codex", "pi", "claude", "agy"])
    def test_opencode_to_all(self, target):
        source = _make_source("opencode", _opencode_records())
        t = self._make_translator_with_source(source)
        plan = t.plan("opencode", target, session_id=_UUID)
        assert len(plan.records) > 0

    @pytest.mark.parametrize("target", ["codex", "pi", "claude", "opencode"])
    def test_agy_to_all(self, target):
        source = _make_source("agy", _agy_records())
        t = self._make_translator_with_source(source)
        plan = t.plan("agy", target, session_id=_UUID)
        assert len(plan.records) > 0

    def test_custom_target_id(self):
        source = _make_source("codex", _codex_records())
        t = self._make_translator_with_source(source)
        custom_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        plan = t.plan("codex", "pi", session_id=_UUID, target_id=custom_id)
        # Session ID should appear in the destination path
        assert custom_id in str(plan.destination)

    def test_plan_is_dry_run(self, tmp_path):
        """plan() must not create any files."""
        source = _make_source("codex", _codex_records())
        t = self._make_translator_with_source(source)
        plan = t.plan("codex", "pi", session_id=_UUID)
        assert not plan.destination.exists()

    def test_preserve_ids_false(self):
        source = _make_source("codex", _codex_records())
        t = SessionTranslator(preserve_ids=False)

        class FakeStore:
            provider_name = "codex"
            def load(self, session_id): return source
            def list(self): return []

        t._codex = FakeStore()
        plan = t.plan("codex", "pi", session_id=_UUID)
        # With preserve_ids=False a new UUID is generated → different from source
        records_str = str(plan.records)
        assert _UUID not in records_str or plan.destination != t._pi.destination_path(_UUID, _CWD, _TS)

    def test_message_content_preserved(self):
        """Text content survives a full codex→claude round-trip (in memory)."""
        source = _make_source("codex", _codex_records())
        t = self._make_translator_with_source(source)
        plan = t.plan("codex", "claude", session_id=_UUID)
        all_text = " ".join(
            r.get("message", {}).get("content", [{}])[0].get("text", "")
            if isinstance(r.get("message", {}).get("content"), list)
            else ""
            for r in plan.records
        )
        assert "What is 2+2?" in all_text
        assert "It is 4." in all_text


@pytest.mark.parametrize("target", ["codex", "pi", "claude", "opencode", "agy", "markdown"])
def test_plans_write_to_custom_roots_using_explicit_format(tmp_path, target):
    import json

    source = tmp_path / "source.jsonl"
    source.write_text("\n".join(map(json.dumps, _codex_records())) + "\n")
    # A directory name must not choose the serialization format.
    destination_root = tmp_path / ".claude" / "custom"
    translator = SessionTranslator(
        codex_home=destination_root, pi_home=destination_root, claude_home=destination_root,
        opencode_data_home=destination_root, agy_brain_dir=destination_root,
        markdown_export_dir=destination_root,
    )
    plan = translator.plan_from_path("codex", target, path=source)
    assert plan.target_provider == target
    assert not plan.destination.exists()
    assert translator.write(plan) == plan.destination
    first = plan.destination.read_bytes()
    assert first
    with pytest.raises(FileExistsError):
        translator.write(plan)
    assert plan.destination.read_bytes() == first
    translator.write(plan, overwrite=True)
    assert plan.destination.read_bytes() == first


def test_opencode_writer_uses_json_array_even_under_claude_directory(tmp_path):
    import json

    from thearc.learning.sessions.translation import ConversionPlan

    records = [{"id": "first"}, {"id": "second"}]
    plan = ConversionPlan(source=_make_source("codex"), destination=tmp_path / ".claude" / "output.json",
                          records=records, target_provider="opencode")
    SessionTranslator().write(plan)
    assert json.loads(plan.destination.read_text()) == records
