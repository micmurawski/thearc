"""Session store implementations: read native session files per provider.

Each store provides:
    - ``list()`` → ``list[SessionSummary]``
    - ``load(session_id)`` → ``NativeSession``
    - ``load_path(path)`` → ``NativeSession``
    - ``destination_path(...)`` → ``Path``  (for output planning)
    - ``write(path, records)`` → ``None``

Supported stores:
    - ``CodexStore``       — ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``
    - ``PiStore``          — ``~/.pi/agent/sessions/--<cwd>--/<uuid>.jsonl``
    - ``ClaudeStore``      — ``~/.claude/projects/<sanitized-cwd>/<uuid>.jsonl``
    - ``OpenCodeStore``    — ``~/.local/share/opencode/session-export/<ses_...>.json``
    - ``AntigravityStore`` — ``~/.gemini/antigravity-cli/brain/<conv-id>/.../transcript.jsonl``
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC
from pathlib import Path

from .models import (
    JsonObject,
    NativeSession,
    SessionSummary,
    _as_object,
    _string_value,
    codex_date_parts,
    codex_filename_timestamp,
    encode_pi_cwd,
    now_iso,
    pi_filename_timestamp,
    sanitize_claude_cwd,
)

# ---------------------------------------------------------------------------
# Optional fast JSON
# ---------------------------------------------------------------------------

try:
    import orjson as _orjson  # type: ignore[import-untyped]
    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False


def _json_loads(data: str | bytes) -> object:
    if _HAS_ORJSON:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return _orjson.loads(data)
    return json.loads(data)


def _json_dumps(obj: object) -> str:
    if _HAS_ORJSON:
        return _orjson.dumps(obj).decode("utf-8")
    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[JsonObject]:
    records: list[JsonObject] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = _json_loads(stripped)
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"warning: {path}:{line_no}: {exc}", file=sys.stderr)
                continue
            if isinstance(value, dict):
                records.append(value)  # type: ignore[arg-type]
    return records


def _write_jsonl(path: Path, records: list[JsonObject], *, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with path.open(mode, encoding="utf-8") as fh:
        for record in records:
            fh.write(_json_dumps(record) + "\n")


def _write_json(path: Path, obj: object, *, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with path.open(mode, encoding="utf-8") as fh:
        fh.write(_json_dumps(obj) + "\n")


# ---------------------------------------------------------------------------
# Codex store
# ---------------------------------------------------------------------------

class CodexStore:
    """Read/write OpenAI Codex CLI session JSONL files.

    Session directory layout::

        ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
    """

    provider_name = "codex"

    def __init__(self, codex_home: Path | None = None) -> None:
        self._home = codex_home or (Path.home() / ".codex")
        self._path_cache: list[Path] | None = None
        self._id_index: dict[str, Path] | None = None

    @property
    def root(self) -> Path:
        return self._home

    # --- public API -------------------------------------------------------

    def list(self) -> list[SessionSummary]:
        results: list[SessionSummary] = []
        for path in self._session_paths():
            try:
                meta = self._read_head_meta(path)
                session_id = _string_value(meta, "id") or self._id_from_filename(path)
                cwd = _string_value(meta, "cwd") or ""
                timestamp = _string_value(meta, "timestamp") or self._timestamp_from_file(path)
                results.append(SessionSummary("codex", session_id, cwd, timestamp, path, -1))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"warning: skipped unreadable Codex session {path}: {exc}", file=sys.stderr)
        return results

    def load(self, session_id: str) -> NativeSession:
        path = self._find_path(session_id)
        if path is None:
            raise FileNotFoundError(f"Codex session not found: {session_id!r}")
        return self.load_path(path)

    def load_path(self, path: Path) -> NativeSession:
        records = self._normalize_records(_read_jsonl(path))
        meta = self._first_payload(records, "session_meta")
        session_id = _string_value(meta, "id") or self._id_from_filename(path)
        cwd = _string_value(meta, "cwd") or ""
        timestamp = _string_value(meta, "timestamp") or self._timestamp_from_file(path)
        return NativeSession("codex", session_id, cwd, timestamp, path, records)

    def destination_path(self, session_id: str, timestamp: str) -> Path:
        year, month, day = codex_date_parts(timestamp)
        filename = f"rollout-{codex_filename_timestamp(timestamp)}-{session_id}.jsonl"
        return self._home / "sessions" / year / month / day / filename

    def write(self, path: Path, records: list[JsonObject], *, overwrite: bool = False) -> None:
        _write_jsonl(path, records, overwrite=overwrite)

    # --- internals --------------------------------------------------------

    def _session_paths(self) -> list[Path]:
        if self._path_cache is None:
            paths: list[Path] = []
            for subdir in ("sessions", "archived_sessions"):
                root = self._home / subdir
                if root.exists():
                    paths.extend(root.rglob("*.jsonl"))
            paths.sort()
            self._path_cache = paths
        return self._path_cache

    def _find_path(self, session_id: str) -> Path | None:
        if self._id_index is None:
            self._id_index = {
                sid: p
                for p in self._session_paths()
                if (sid := self._id_from_filename(p))
            }
        if session_id in self._id_index:
            return self._id_index[session_id]
        for p in self._session_paths():
            if session_id in p.name:
                return p
        return None

    @staticmethod
    def _id_from_filename(path: Path) -> str:
        parts = path.stem.rsplit("-", 1)
        return parts[-1] if parts else path.stem

    @staticmethod
    def _timestamp_from_file(path: Path) -> str:
        # Derive from file mtime or return now
        try:
            mtime = path.stat().st_mtime
            from datetime import datetime
            return datetime.fromtimestamp(mtime, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except OSError:
            return now_iso()

    @staticmethod
    def _read_head_meta(path: Path) -> JsonObject:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                if line_no > 200:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                value = _json_loads(stripped)
                if not isinstance(value, dict):
                    continue
                record = _as_object(value)
                if record is None:
                    continue
                if record.get("type") == "session_meta":
                    payload = _as_object(record.get("payload"))
                    if payload is not None:
                        return payload
                if line_no == 1 and "type" not in record and "id" in record:
                    return record
        return {}

    @staticmethod
    def _first_payload(records: list[JsonObject], record_type: str) -> JsonObject:
        for record in records:
            if record.get("type") == record_type:
                payload = _as_object(record.get("payload"))
                if payload is not None:
                    return payload
        return {}

    @staticmethod
    def _normalize_records(records: list[JsonObject]) -> list[JsonObject]:
        """Normalize old flat Codex format to new wrapped format."""
        if not records:
            return records
        first = records[0]
        if first.get("type") == "session_meta":
            return records
        if "type" not in first and "id" in first:
            wrapper: JsonObject = {
                "type": "session_meta",
                "timestamp": first.get("timestamp", ""),
                "payload": {
                    "id": first.get("id", ""),
                    "timestamp": first.get("timestamp", ""),
                    "cwd": first.get("cwd", ""),
                },
            }
            return [wrapper] + records[1:]
        return records


# ---------------------------------------------------------------------------
# Pi store
# ---------------------------------------------------------------------------

class PiStore:
    """Read/write Pi coding agent session JSONL files.

    Session directory layout::

        ~/.pi/agent/sessions/--<encoded-cwd>--/<uuid>.jsonl
    """

    provider_name = "pi"

    def __init__(self, pi_home: Path | None = None) -> None:
        self._home = pi_home or (Path.home() / ".pi")
        self._agent_dir = self._home / "agent" / "sessions"

    @property
    def root(self) -> Path:
        return self._agent_dir

    def list(self) -> list[SessionSummary]:
        results: list[SessionSummary] = []
        if not self._agent_dir.exists():
            return results
        for path in sorted(self._agent_dir.rglob("*.jsonl")):
            try:
                session = self.load_path(path)
                results.append(session.summary())
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"warning: skipped Pi session {path}: {exc}", file=sys.stderr)
        return results

    def load(self, session_id: str) -> NativeSession:
        if self._agent_dir.exists():
            for path in self._agent_dir.rglob("*.jsonl"):
                if session_id in path.name:
                    return self.load_path(path)
        raise FileNotFoundError(f"Pi session not found: {session_id!r}")

    def load_path(self, path: Path) -> NativeSession:
        records = _read_jsonl(path)
        cwd = self._extract_cwd(path, records)
        session_id = path.stem
        timestamp = self._extract_timestamp(records) or self._timestamp_from_file(path)
        return NativeSession("pi", session_id, cwd, timestamp, path, records)

    def destination_path(self, session_id: str, cwd: str, timestamp: str) -> Path:
        cwd_dir = encode_pi_cwd(cwd) if cwd else "--unknown--"
        filename = f"{pi_filename_timestamp(timestamp)}-{session_id}.jsonl"
        return self._agent_dir / cwd_dir / filename

    def write(self, path: Path, records: list[JsonObject], *, overwrite: bool = False) -> None:
        _write_jsonl(path, records, overwrite=overwrite)

    @staticmethod
    def _extract_cwd(path: Path, records: list[JsonObject]) -> str:
        for record in records:
            if record.get("type") == "session_meta":
                cwd = _string_value(_as_object(record.get("payload")) or record, "cwd")
                if cwd:
                    return cwd
            cwd = _string_value(record, "cwd")
            if cwd:
                return cwd
        # Decode from directory name
        parent_name = path.parent.name
        if parent_name.startswith("--") and parent_name.endswith("--"):
            inner = parent_name[2:-2]
            return "/" + inner.replace("-", "/")
        return ""

    @staticmethod
    def _extract_timestamp(records: list[JsonObject]) -> str | None:
        for record in records:
            ts = _string_value(record, "timestamp")
            if ts:
                return ts
        return None

    @staticmethod
    def _timestamp_from_file(path: Path) -> str:
        try:
            mtime = path.stat().st_mtime
            from datetime import datetime
            return datetime.fromtimestamp(mtime, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except OSError:
            return now_iso()


# ---------------------------------------------------------------------------
# Claude store
# ---------------------------------------------------------------------------

class ClaudeStore:
    """Read/write Claude Code / Anthropic CLI session JSONL files.

    Session directory layout::

        ~/.claude/projects/<sanitized-cwd>/<uuid>.jsonl
    """

    provider_name = "claude"

    def __init__(self, claude_home: Path | None = None) -> None:
        self._home = claude_home or Path(
            os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
        )
        self._projects_dir = self._home / "projects"

    @property
    def root(self) -> Path:
        return self._projects_dir

    def list(self) -> list[SessionSummary]:
        results: list[SessionSummary] = []
        if not self._projects_dir.exists():
            return results
        for path in sorted(self._projects_dir.rglob("*.jsonl")):
            try:
                session = self.load_path(path)
                results.append(session.summary())
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"warning: skipped Claude session {path}: {exc}", file=sys.stderr)
        return results

    def load(self, session_id: str) -> NativeSession:
        if self._projects_dir.exists():
            for path in self._projects_dir.rglob("*.jsonl"):
                if session_id in path.stem:
                    return self.load_path(path)
        raise FileNotFoundError(f"Claude session not found: {session_id!r}")

    def load_path(self, path: Path) -> NativeSession:
        records = _read_jsonl(path)
        session_id = path.stem
        cwd = self._extract_cwd(path, records)
        timestamp = self._extract_timestamp(records) or self._timestamp_from_file(path)
        return NativeSession("claude", session_id, cwd, timestamp, path, records)

    def destination_path(self, session_id: str, cwd: str) -> Path:
        sanitized = sanitize_claude_cwd(cwd) if cwd else "unknown"
        return self._projects_dir / sanitized / f"{session_id}.jsonl"

    def write(self, path: Path, records: list[JsonObject], *, overwrite: bool = False) -> None:
        _write_jsonl(path, records, overwrite=overwrite)

    @staticmethod
    def _extract_cwd(path: Path, records: list[JsonObject]) -> str:
        for record in records:
            cwd = _string_value(record, "cwd")
            if cwd:
                return cwd
        # Decode from sanitized dir name (best effort: replace "-" → "/")
        parent = path.parent.name
        if parent:
            return parent.replace("-", "/")
        return ""

    @staticmethod
    def _extract_timestamp(records: list[JsonObject]) -> str | None:
        for record in records:
            ts = _string_value(record, "timestamp")
            if ts:
                return ts
        return None

    @staticmethod
    def _timestamp_from_file(path: Path) -> str:
        try:
            mtime = path.stat().st_mtime
            from datetime import datetime
            return datetime.fromtimestamp(mtime, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except OSError:
            return now_iso()


# ---------------------------------------------------------------------------
# OpenCode store
# ---------------------------------------------------------------------------

class OpenCodeStore:
    """Read/write OpenCode (SST) session export JSON files.

    Session directory layout::

        ~/.local/share/opencode/session-export/<ses_...>.json
    """

    provider_name = "opencode"

    def __init__(self, opencode_data_home: Path | None = None) -> None:
        if opencode_data_home is not None:
            self._data_home = opencode_data_home
        else:
            env = os.environ.get("OPENCODE_GLOBAL_DATA_DIR")
            if env:
                self._data_home = Path(env)
            elif sys.platform == "win32":
                appdata = os.environ.get("APPDATA")
                base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
                self._data_home = base / "opencode"
            else:
                xdg = os.environ.get("XDG_DATA_HOME")
                self._data_home = (Path(xdg) / "opencode") if xdg else Path.home() / ".local" / "share" / "opencode"
        self._session_dir = self._data_home / "session-export"

    @property
    def root(self) -> Path:
        return self._session_dir

    def list(self) -> list[SessionSummary]:
        results: list[SessionSummary] = []
        if not self._session_dir.exists():
            return results
        for path in sorted(self._session_dir.glob("*.json")):
            try:
                session = self.load_path(path)
                results.append(session.summary())
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"warning: skipped OpenCode session {path}: {exc}", file=sys.stderr)
        return results

    def load(self, session_id: str) -> NativeSession:
        # Try exact match first
        exact = self._session_dir / f"{session_id}.json"
        if exact.exists():
            return self.load_path(exact)
        if self._session_dir.exists():
            for path in self._session_dir.glob("*.json"):
                if session_id in path.stem:
                    return self.load_path(path)
        raise FileNotFoundError(f"OpenCode session not found: {session_id!r}")

    def load_path(self, path: Path) -> NativeSession:
        with path.open("r", encoding="utf-8") as fh:
            data = _json_loads(fh.read())
        records = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
        session_id = path.stem
        if isinstance(data, dict):
            session_id = _string_value(data, "id") or session_id  # type: ignore[arg-type]
            cwd = _string_value(data, "cwd") or ""  # type: ignore[arg-type]
            timestamp = _string_value(data, "createdAt") or now_iso()  # type: ignore[arg-type]
        else:
            cwd = ""
            timestamp = now_iso()
        return NativeSession("opencode", session_id, cwd, timestamp, path, records)  # type: ignore[arg-type]

    def destination_path(self, session_id: str) -> Path:
        return self._session_dir / f"{session_id}.json"

    def write(self, path: Path, records: list[JsonObject], *, overwrite: bool = False) -> None:
        obj = records[0] if len(records) == 1 else records
        _write_json(path, obj, overwrite=overwrite)


# ---------------------------------------------------------------------------
# Antigravity (agy) store
# ---------------------------------------------------------------------------

class AntigravityStore:
    """Read Antigravity (agy / Google Deepmind CLI) session transcript files.

    Session directory layout::

        ~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript.jsonl

    Writing creates a simplified JSONL file suitable for Claude Code import
    (same schema as ClaudeStore but tagged with ``agy`` provenance metadata).
    """

    provider_name = "agy"

    def __init__(self, brain_dir: Path | None = None) -> None:
        self._brain_dir = brain_dir or (Path.home() / ".gemini" / "antigravity-cli" / "brain")

    @property
    def root(self) -> Path:
        return self._brain_dir

    def list(self) -> list[SessionSummary]:
        results: list[SessionSummary] = []
        if not self._brain_dir.exists():
            return results
        for transcript in self._find_transcripts():
            try:
                session = self.load_path(transcript)
                results.append(session.summary())
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"warning: skipped Antigravity session {transcript}: {exc}", file=sys.stderr)
        return results

    def load(self, session_id: str) -> NativeSession:
        for transcript in self._find_transcripts():
            # conversation ID is the parent directory name of .system_generated
            conv_id = transcript.parents[2].name
            if session_id == conv_id or session_id in transcript.as_posix():
                return self.load_path(transcript)
        raise FileNotFoundError(f"Antigravity session not found: {session_id!r}")

    def load_path(self, path: Path) -> NativeSession:
        records = _read_jsonl(path)
        # Conversation ID is the grandparent of .system_generated/logs/transcript.jsonl
        try:
            conv_id = path.parents[2].name
        except IndexError:
            conv_id = path.stem
        cwd = self._extract_cwd(records)
        timestamp = self._extract_timestamp(records) or self._timestamp_from_file(path)
        return NativeSession("agy", conv_id, cwd, timestamp, path, records)

    def destination_path(self, session_id: str, cwd: str) -> Path:
        """Antigravity write target: a JSONL sidecar next to the brain folder."""
        sanitized = sanitize_claude_cwd(cwd) if cwd else "unknown"
        return self._brain_dir / "exports" / sanitized / f"{session_id}.jsonl"

    def write(self, path: Path, records: list[JsonObject], *, overwrite: bool = False) -> None:
        _write_jsonl(path, records, overwrite=overwrite)

    def _find_transcripts(self) -> list[Path]:
        return sorted(self._brain_dir.rglob(".system_generated/logs/transcript.jsonl"))

    @staticmethod
    def _extract_cwd(records: list[JsonObject]) -> str:
        for record in records:
            # Antigravity stores cwd in USER_INPUT steps sometimes
            cwd = _string_value(record, "cwd")
            if cwd:
                return cwd
        return ""

    @staticmethod
    def _extract_timestamp(records: list[JsonObject]) -> str | None:
        for record in records:
            ts = _string_value(record, "created_at")
            if ts:
                return ts
        return None

    @staticmethod
    def _timestamp_from_file(path: Path) -> str:
        try:
            mtime = path.stat().st_mtime
            from datetime import datetime
            return datetime.fromtimestamp(mtime, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except OSError:
            return now_iso()


# ---------------------------------------------------------------------------
# Markdown export store
# ---------------------------------------------------------------------------

class MarkdownStore:
    """Write sessions as readable Markdown files.
    
    This store is write-only. Default output is ``~/.gemini/antigravity-cli/session-exports/``.
    """

    provider_name = "markdown"

    def __init__(self, export_dir: Path | None = None) -> None:
        self._export_dir = export_dir or (Path.home() / ".gemini" / "antigravity-cli" / "session-exports")

    @property
    def root(self) -> Path:
        return self._export_dir

    def list(self) -> list[SessionSummary]:
        return []

    def load(self, session_id: str) -> NativeSession:
        raise NotImplementedError("MarkdownStore is write-only.")

    def load_path(self, path: Path) -> NativeSession:
        raise NotImplementedError("MarkdownStore is write-only.")

    def destination_path(self, session_id: str, *args, **kwargs) -> Path:
        return self._export_dir / f"{session_id}.md"

    def write(self, path: Path, records: list[JsonObject], *, overwrite: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if overwrite else "x"
        with path.open(mode, encoding="utf-8") as fh:
            fh.write(records[0]["markdown"])

