"""Exports: events written in a harness's own transcript format, or as canonical ``thearc`` JSONL.

Every export maps events to blocks once (``blocks``), then one small writer per format
lays them out (``codex``, ``claude``, ``pi``, ``opencode``, ``antigravity``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..events import EventLike, UnsupportedConversion, codex_control, provenance_only, records
from . import antigravity, claude, codex, opencode, pi
from .blocks import Block, Conversion, to_blocks
from .identity import Identity, identity

FORMATS = ("thearc", "codex", "claude", "pi", "opencode", "antigravity")
ALIASES = {"agy": "antigravity"}
Writer = Callable[[list[Block], Conversion, Identity], tuple[list[dict[str, Any]], list[str]]]
"""Lays blocks out in one format: ``(records, notes)``."""
_WRITERS: dict[str, Writer] = {
    "codex": codex.write,
    "claude": claude.write,
    "pi": pi.write,
    "opencode": opencode.write,
    "antigravity": antigravity.write,
}


@dataclass(frozen=True)
class Export:
    """Exported text, the events it contains, and every transformation applied.

    ``session_id`` is the session's ID inside the export (as the target harness will
    know it), ``cwd`` its working directory, and ``started_at`` its first timestamp.
    """

    format: str
    text: str
    event_ids: tuple[str, ...]
    transformations: tuple[str, ...] = ()
    session_id: str | None = None
    cwd: str = ""
    started_at: str | None = None

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(self.text)
        return path


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def export_events(
    events: EventLike, format: str = "thearc", *, strict: bool = True, session_id: str | None = None
) -> Export:
    """Convert events to one of ``FORMATS`` (``agy`` is short for ``antigravity``).

    Strict conversion refuses anything the target cannot represent faithfully; with
    ``strict=False`` such events are kept as labelled text and listed in ``transformations``.
    ``session_id`` sets the session's ID in the target, e.g. to install a copy beside the
    original; by default the native ID is kept (or a UUID / ``ses_`` ID derived from it).
    """
    format = ALIASES.get(format, format)
    if format not in FORMATS:
        raise UnsupportedConversion(f"Unsupported format: {format}")
    events = records(events)
    if format == "thearc":
        return Export(format, _jsonl(events), tuple(e["id"] for e in events))
    # Codex keeps its own runtime records; other metadata has no place in a transcript.
    kept = [e for e in events if not provenance_only(e) or (format == "codex" and codex_control(e))]
    omitted = len(events) - len(kept)
    notes = (
        [f"{omitted} metadata record(s) without a {format} representation omitted; they remain in the index."]
        if omitted
        else []
    )
    conversion = Conversion(strict)
    target = identity(kept, format, session_id)
    output, written = _WRITERS[format](to_blocks(kept, conversion, format), conversion, target)
    return Export(
        format,
        _jsonl(output),
        tuple(e["id"] for e in kept),
        (*notes, *written, *conversion.notes),
        target.session_id,
        target.cwd,
        target.started,
    )


__all__ = ["ALIASES", "FORMATS", "Export", "UnsupportedConversion", "export_events"]
