"""Session IDs and timestamps as each harness format needs them."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

_BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def epoch_ms(timestamp: str | None) -> int | None:
    try:
        return int(datetime.fromisoformat(str(timestamp)).timestamp() * 1000)
    except ValueError:
        return None


def first_ms(events: Sequence[dict]) -> int:
    """The first parseable event timestamp, in epoch milliseconds; 0 when there is none."""
    return next((ms for e in events if e["timestamp"] and (ms := epoch_ms(e["timestamp"])) is not None), 0)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Clock:
    """Event times in milliseconds, carrying the last known time over events that have none."""

    def __init__(self) -> None:
        self.ms = 0

    def __call__(self, event: dict) -> int:
        ms = epoch_ms(event["timestamp"]) if event["timestamp"] else None
        self.ms = ms if ms is not None else self.ms
        return self.ms


def session_uuid(native_id: str) -> str:
    """The native ID if it is a UUID (Claude and Codex require one), else a stable UUID derived from it."""
    try:
        return str(UUID(str(native_id)))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"thearc-session:{native_id}"))


def opencode_id(prefix: str, ms: int, seed: str) -> str:
    """OpenCode-shaped ID: a time-ordered hex part and a suffix derived from ``seed``, so exports repeat."""
    encoded = (ms * 0x1000 + 1) & ((1 << 48) - 1)
    if prefix == "ses":
        encoded = ~encoded & ((1 << 48) - 1)  # OpenCode lists sessions newest first.
    number, suffix = int.from_bytes(hashlib.sha256(f"{prefix}:{seed}".encode()).digest(), "big"), ""
    for _ in range(14):
        number, digit = divmod(number, 62)
        suffix += _BASE62[digit]
    return f"{prefix}_{encoded:012x}{suffix}"


@dataclass(frozen=True)
class Identity:
    """The exported session's ID in the target format, its working directory, and when it started."""

    session_id: str
    cwd: str
    started: str


def identity(events: Sequence[dict], format: str, session_id: str | None = None) -> Identity:
    first = events[0] if events else {}
    native = str(first.get("session_native_id") or first.get("session_id") or "session")
    cwd = next((e["cwd"] for e in events if e.get("cwd")), "")
    started = first_ms(events)
    if session_id is None:
        if format in {"codex", "claude"}:
            session_id = session_uuid(native)
        elif format == "opencode":
            session_id = native if native.startswith("ses_") else opencode_id("ses", started, native)
        else:
            session_id = native
    elif format in {"codex", "claude"} and session_uuid(session_id) != session_id:
        raise ValueError(f"{format} session IDs must be UUIDs: {session_id!r}")
    elif format == "opencode" and not session_id.startswith("ses_"):
        raise ValueError(f"OpenCode session IDs start with 'ses_': {session_id!r}")
    return Identity(session_id, cwd, iso(started))
