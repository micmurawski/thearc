"""Installing exports where a harness looks for sessions, so it can resume or fork them.

An installed session is a **copy**: by default it gets its own session ID, derived
from the exported events and the target harness, so it never collides with the
original and installing the same selection again finds the earlier copy. An existing
session with the same ID is never replaced unless ``overwrite=True``.

| Harness | Location (default home) | Resume |
| --- | --- | --- |
| codex | ``~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl`` (``$CODEX_HOME``) | ``codex resume <id>`` |
| claude | ``~/.claude/projects/<cwd as name>/<id>.jsonl`` (``$CLAUDE_CONFIG_DIR``) | ``claude --resume <id>`` in cwd |
| pi | ``~/.pi/agent/sessions/--<cwd>--/<time>_<id>.jsonl`` | ``pi --session <file>`` |
| opencode | ``~/.local/share/opencode/thearc-imports/<id>.json`` (needs an import) | ``opencode import <file>`` |

Antigravity has no way to load a transcript, so it cannot be installed into.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .events import EventLike, records
from .export import Export, export_events
from .export.identity import first_ms, opencode_id

INSTALLABLE = ("codex", "claude", "pi", "opencode")


class SessionAlreadyInstalled(FileExistsError):
    """The harness already has a session with this ID."""


@dataclass(frozen=True)
class InstalledSession:
    """Where an export was written and how to open it.

    ``imported`` is false when the harness still has to load the file itself
    (OpenCode: run ``command``).
    """

    harness: str
    session_id: str
    path: Path
    command: str
    imported: bool = True
    transformations: tuple[str, ...] = ()


def default_home(harness: str) -> Path:
    if harness == "codex":
        return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
    if harness == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    if harness == "pi":
        return Path("~/.pi/agent").expanduser()
    if harness == "opencode":
        data = os.environ.get("XDG_DATA_HOME")
        return (Path(data) if data else Path("~/.local/share").expanduser()) / "opencode"
    raise ValueError(f"cannot install sessions into {harness!r}; installable: {', '.join(INSTALLABLE)}")


def copy_session_id(events: EventLike, harness: str) -> str:
    """A stable ID for a copy of exactly these events in ``harness``: same selection, same ID."""
    events = records(events)
    seed = hashlib.sha256(json.dumps([harness, [e["id"] for e in events]]).encode()).hexdigest()
    if harness == "opencode":
        return opencode_id("ses", first_ms(events), seed)
    return str(uuid5(NAMESPACE_URL, f"thearc-copy:{seed}"))


def _started(export: Export) -> datetime:
    return datetime.fromisoformat(export.started_at) if export.started_at else datetime.fromtimestamp(0, UTC)


def _encode_pi_cwd(cwd: str) -> str:
    return "--" + cwd.lstrip("/\\").replace("/", "-").replace("\\", "-").replace(":", "-") + "--"


def _claude_project(cwd: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in cwd)


def _locate(export: Export, home: Path) -> tuple[Path, list[Path], str, bool]:
    """``(destination, existing files with this session ID, command, imported)``."""
    session_id, started = export.session_id, _started(export).astimezone(UTC)
    if export.format == "codex":
        base = home / "sessions"
        name = f"rollout-{started:%Y-%m-%dT%H-%M-%S}-{session_id}.jsonl"
        destination = base / f"{started:%Y}" / f"{started:%m}" / f"{started:%d}" / name
        existing = sorted(base.rglob(f"rollout-*-{session_id}.jsonl")) if base.is_dir() else []
        return destination, existing, f"codex resume {session_id}", True
    if export.format == "claude":
        if not export.cwd:
            raise ValueError("Claude Code files sessions by working directory, and this session has none")
        base = home / "projects"
        existing = sorted(base.glob(f"*/{session_id}.jsonl")) if base.is_dir() else []
        command = f"cd {shlex.quote(export.cwd)} && claude --resume {session_id}"
        return base / _claude_project(export.cwd) / f"{session_id}.jsonl", existing, command, True
    if export.format == "pi":
        base = home / "sessions"
        stamp = started.isoformat(timespec="milliseconds").replace("+00:00", "Z").replace(":", "-").replace(".", "-")
        destination = base / _encode_pi_cwd(export.cwd or "unknown") / f"{stamp}_{session_id}.jsonl"
        existing = sorted(base.glob(f"*/*_{session_id}.jsonl")) if base.is_dir() else []
        return destination, existing, f"pi --session {shlex.quote(str(destination))}", True
    if export.format == "opencode":
        destination = home / "thearc-imports" / f"{session_id}.json"
        return destination, [destination] if destination.exists() else [], \
            f"opencode import {shlex.quote(str(destination))}", False
    raise ValueError(f"cannot install sessions into {export.format!r}; installable: {', '.join(INSTALLABLE)}")


def install_export(export: Export, *, home: str | Path | None = None, overwrite: bool = False) -> InstalledSession:
    """Write ``export`` where its harness looks for sessions; refuse to replace one unless ``overwrite``."""
    if export.format not in INSTALLABLE:
        raise ValueError(f"cannot install sessions into {export.format!r}; installable: {', '.join(INSTALLABLE)}")
    if not export.session_id or any(char in export.session_id for char in "/\\") or export.session_id in {".", ".."}:
        raise ValueError(f"unsafe session ID for a file name: {export.session_id!r}")
    home = Path(home).expanduser() if home else default_home(export.format)
    destination, existing, command, imported = _locate(export, home)
    if existing and not overwrite:
        raise SessionAlreadyInstalled(f"{export.format} already has session {export.session_id} at {existing[0]}; "
                                      "use overwrite to replace it")
    for old in existing:
        if old != destination:
            old.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".thearc-tmp")
    temporary.write_text(export.text, encoding="utf-8")
    temporary.replace(destination)
    return InstalledSession(export.format, export.session_id, destination, command, imported)


def install_events(events: EventLike, harness: str, *, home: str | Path | None = None, overwrite: bool = False,
                   strict: bool = True, keep_id: bool = False) -> InstalledSession:
    """Export ``events`` to ``harness`` and install the result; a copy ID is used unless ``keep_id``."""
    if harness not in INSTALLABLE:
        raise ValueError(f"cannot install sessions into {harness!r}; installable: {', '.join(INSTALLABLE)}")
    session_id = None if keep_id else copy_session_id(events, harness)
    export = export_events(events, harness, strict=strict, session_id=session_id)
    installed = install_export(export, home=home, overwrite=overwrite)
    return replace(installed, transformations=export.transformations)
