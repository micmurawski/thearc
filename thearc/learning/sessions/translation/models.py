"""Lightweight data models for the session translation layer.

These types describe text-message conversion. Indexed Event records preserve
tool activity and provenance; translating messages does not preserve that full
event stream or create a native fork.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

JsonObject = dict[str, Any]


def _as_object(value: object) -> JsonObject | None:
    if isinstance(value, dict):
        return value  # type: ignore[return-value]
    return None


def _as_list(value: object) -> list[Any] | None:
    if isinstance(value, list):
        return value
    return None


def _string_value(obj: JsonObject, key: str) -> str | None:
    val = obj.get(key)
    if isinstance(val, str):
        return val
    return None


# ---------------------------------------------------------------------------
# ID / path utilities (ported from vibheksoni/session-export session_sdk/paths.py)
# ---------------------------------------------------------------------------

_BASE62_CHARS = string.digits + string.ascii_lowercase + string.ascii_uppercase


def _random_base62(length: int) -> str:
    import secrets
    return "".join(secrets.choice(_BASE62_CHARS) for _ in range(length))


def is_uuid(value: str) -> bool:
    """Return True when *value* is a valid UUID string."""
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def iso_to_epoch_ms(timestamp: str) -> int:
    """Convert an ISO-8601 timestamp string to epoch milliseconds."""
    if not timestamp:
        return int(datetime.now(UTC).timestamp() * 1000)
    normalized = timestamp.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except ValueError:
        return int(datetime.now(UTC).timestamp() * 1000)


def epoch_ms_to_iso(value: int) -> str:
    """Convert epoch milliseconds to a UTC ISO-8601 string (e.g. ``2024-01-01T12:00:00.000Z``)."""
    return (
        datetime.fromtimestamp(value / 1000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def opencode_id(prefix: Literal["ses", "msg", "prt"], timestamp: str) -> str:
    """Generate an OpenCode-style prefixed ID (``ses_<hex14><base62-14>``)."""
    epoch_ms = iso_to_epoch_ms(timestamp)
    encoded = (epoch_ms * 0x1000 + 1) & ((1 << 48) - 1)
    if prefix == "ses":
        encoded = ~encoded & ((1 << 48) - 1)
    hex_part = f"{encoded:012x}"
    return f"{prefix}_{hex_part}{_random_base62(14)}"


def opencode_slug(text: str) -> str:
    """Derive a short URL slug from session title text."""
    value = "".join(ch.lower() if ch.isalnum() else "-" for ch in text.strip())
    parts = [part for part in value.split("-") if part]
    return "-".join(parts[:8]) or "imported-session"


def sanitize_claude_cwd(cwd: str) -> str:
    """Sanitize a working-directory path for use as a Claude session directory name."""
    sanitized = cwd.replace("\\?\\", "")
    sanitized = "".join(ch if ch.isalnum() else "-" for ch in sanitized)
    return sanitized


def encode_pi_cwd(cwd: str) -> str:
    """Encode a cwd path to a Pi session directory name (``--<encoded>--``)."""
    cwd = cwd.removeprefix("\\\\?\\")
    p = Path(cwd)
    if p.is_absolute():
        resolved = str(p.resolve())
        resolved = resolved.removeprefix("\\\\?\\")
    else:
        resolved = cwd
    stripped = resolved.lstrip("/\\")
    encoded = stripped.replace("/", "-").replace("\\", "-").replace(":", "-")
    return f"--{encoded}--"


def pi_filename_timestamp(timestamp: str) -> str:
    return timestamp.replace(":", "-").replace(".", "-")


def codex_filename_timestamp(timestamp: str) -> str:
    return timestamp.replace(":", "-").replace(".", "-").replace("Z", "")


def codex_date_parts(timestamp: str) -> tuple[str, str, str]:
    date_part = timestamp.split("T", 1)[0]
    year, month, day = date_part.split("-", 2)
    return year, month, day


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Core session data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Lightweight listing summary – no full record payload loaded."""
    provider: str
    session_id: str
    cwd: str
    timestamp: str
    path: Path
    message_count: int


@dataclass(frozen=True, slots=True)
class TextMessage:
    """Provider-neutral message extracted from a native session."""
    role: str           # "user" | "assistant"
    text: str
    timestamp: str
    model: str | None = None
    provider: str | None = None
    api: str | None = None
    is_compaction: bool = False   # True for compaction/summary boundaries
    is_contextual: bool = False   # True for injected system context messages


@dataclass(frozen=True, slots=True)
class NativeSession:
    """Raw provider session with unparsed JSONL/JSON records."""
    provider: str
    session_id: str
    cwd: str
    timestamp: str
    path: Path
    records: list[JsonObject] = field(repr=False)

    def summary(self, message_count: int = -1) -> SessionSummary:
        return SessionSummary(
            provider=self.provider,
            session_id=self.session_id,
            cwd=self.cwd,
            timestamp=self.timestamp,
            path=self.path,
            message_count=message_count,
        )


@dataclass(frozen=True, slots=True)
class ConversionPlan:
    """Holds the translated records ready to write, without side-effects."""
    source: NativeSession
    destination: Path
    records: list[JsonObject] = field(repr=False)
    target_provider: str
    """Explicit output format; independent of the destination directory name."""
