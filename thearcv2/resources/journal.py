"""The journal: a MetaAgent's append-only history of targets and ratings."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import threading
import time
import warnings
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictFloat, StrictStr, TypeAdapter, ValidationError, field_validator

from thearcv2.resources.ratings import RatingPolicy, RatingScale, Summary, count_all
from thearcv2.resources.targets import ResourceTarget

_clock = threading.Lock()
_last_ns = 0


def new_entry_id() -> str:
    """Time-ordered ID; sorting IDs reproduces recording order, even within one batch."""
    global _last_ns
    with _clock:
        _last_ns = max(time.time_ns(), _last_ns + 1)
        stamp = _last_ns
    return f"{stamp:020d}-{secrets.token_hex(4)}"


def derived_entry_id(at: datetime, *parts: object) -> str:
    """Time-ordered ID derived from content: recording the same thing twice gives the same ID."""
    stamp = int(at.timestamp() * 1_000_000) * 1_000
    return f"{stamp:020d}-{hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:8]}"


def _now() -> datetime:
    return datetime.now(UTC)


class _Entry(BaseModel):
    """Common fields. ``extra`` holds whatever the caller passed as keyword arguments
    (e.g. who caused the entry, or the session a rating came from); it must be JSON."""

    model_config = {"frozen": True, "extra": "forbid"}
    id: str = Field(default_factory=new_entry_id)
    target: ResourceTarget
    revision: str
    at: datetime = Field(default_factory=_now)
    commit: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("extra")
    @classmethod
    def _json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(json.dumps(value, allow_nan=False))
        except (TypeError, ValueError):
            raise ValueError("extra must be JSON-serialisable") from None


class _Change(_Entry):
    """A change to the configuration. ``reason`` says why (e.g. the commit message).

    Texts are what the target owns: a section's own lines without its heading and
    subsections, a whole resource's lines outside its sections, or a hook/MCP entry.
    """

    reason: str | None = None
    parent: str | None = None  # The commit's parent, so a diff can start at a commit that recorded nothing.
    touched: list[str] = Field(default_factory=list)  # IDs of every target the same commit changed.


class Added(_Change):
    kind: Literal["added"] = "added"
    content_hash: str
    content: str = ""


class Modified(_Change):
    kind: Literal["modified"] = "modified"
    before_hash: str
    after_hash: str
    before: str = ""
    after: str = ""

    def diff(self, *, context_lines: int = 3) -> str:
        """Unified diff of this section's text, headed by its address."""
        return _unified(self.before, self.after, self.target, self.target, context_lines)


class Moved(_Change):
    """``target`` is the new address; ``previous`` the old one. History carries over."""

    kind: Literal["moved"] = "moved"
    previous: ResourceTarget


class Removed(_Change):
    kind: Literal["removed"] = "removed"
    content_hash: str
    content: str = ""


Change = Added | Modified | Moved | Removed


class Kept(_Entry):
    """A curator looked at a target its reflection items rated and left it unchanged; ``reason`` says why."""

    kind: Literal["kept"] = "kept"
    reason: str


def _unified(
    before: str | None,
    after: str | None,
    old: ResourceTarget | None,
    new: ResourceTarget | None,
    context_lines: int = 3,
) -> str:
    lines = difflib.unified_diff(
        [] if before is None else before.splitlines(),
        [] if after is None else after.splitlines(),
        fromfile=f"a/{old}" if old else "/dev/null",
        tofile=f"b/{new}" if new else "/dev/null",
        lineterm="",
        n=context_lines,
    )
    return "\n".join(lines)


class Rated(_Entry):
    """One validated reflection item, resolved to one target."""

    kind: Literal["rated"] = "rated"
    scale: str
    value: StrictStr | StrictFloat
    ref: str
    item_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


Entry = Annotated[Added | Modified | Moved | Removed | Rated | Kept, Field(discriminator="kind")]


@dataclass(frozen=True)
class Lineage:
    """One target's history across moves, oldest entry first. ``id`` is the ID the target had when the
    lineage began; it stays the same through renames."""

    target: ResourceTarget
    entries: tuple[Entry, ...]
    removed: bool
    id: str


# Grouped lineages per entry list. Each value keeps its list alive, so an object ID is never reused while cached.
_LINEAGE_CACHE_SIZE = 16
_LINEAGES: OrderedDict[int, tuple[list[Any], int, list[Lineage]]] = OrderedDict()


class Journal(BaseModel):
    """Append-only. Entries are kept in ID order, which is recording order."""

    model_config = {"extra": "forbid"}
    entries: list[Entry] = Field(default_factory=list)

    def record(self, *entries: Entry) -> None:
        known = {entry.id for entry in self.entries}
        for entry in entries:
            if entry.id in known:
                raise ValueError(f"duplicate journal entry: {entry.id}")
            known.add(entry.id)
        self.entries = sorted([*self.entries, *entries], key=lambda entry: entry.id)

    def merge(self, *entries: Entry) -> tuple[Entry, ...]:
        """Record the entries not already in the journal (by ID); returns those. A known ID with different
        content is an error, since entries are never rewritten."""
        known = {entry.id: entry for entry in self.entries}
        new = []
        for entry in entries:
            if entry.id in known:
                if known[entry.id] != entry:
                    raise ValueError(f"journal entry {entry.id} differs from the recorded one")
                continue
            known[entry.id] = entry
            new.append(entry)
        self.record(*new)
        return tuple(new)

    @classmethod
    def load(cls, path: str | Path) -> Journal:
        """Read a journal file; a missing file is an empty journal."""
        path = Path(path)
        return cls(entries=load_journal(path.read_text(encoding="utf-8"), name=path.name) if path.is_file() else [])

    def dump(self, path: str | Path) -> None:
        """Write the whole journal, sorted by ID, replacing the file at once (never half-written)."""
        path = Path(path)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(dump_journal(self.entries), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def append(path: str | Path, entries: Iterable[Entry]) -> None:
        """Add entries to the end of a journal file without reading it; one write, one line per entry.

        Appending an entry that is already there is harmless: reading collapses repeated lines.
        """
        text = "".join(entry.model_dump_json() + "\n" for entry in entries)
        if not text:
            return
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

    def __len__(self) -> int:
        return len(self.entries)

    def lineages(self) -> list[Lineage]:
        """Group entries by target, following Moved entries. A target added again after removal starts anew.

        The grouping is cached until the entries change (``record`` replaces the list), so queries that look at
        many targets — such as a roll-up over subsections — group the journal once instead of once per target.
        """
        cached = _LINEAGES.get(id(self.entries))
        if cached is not None and cached[0] is self.entries and cached[1] == len(self.entries):
            _LINEAGES.move_to_end(id(self.entries))
            return list(cached[2])
        result = self._group()
        _LINEAGES[id(self.entries)] = (self.entries, len(self.entries), result)
        while len(_LINEAGES) > _LINEAGE_CACHE_SIZE:
            _LINEAGES.popitem(last=False)
        return list(result)

    def _group(self) -> list[Lineage]:
        current: dict[str, int] = {}
        groups: list[list[Any]] = []

        def open_group(target: ResourceTarget) -> int:
            groups.append([target, [], False, target.id])
            current[target.id] = len(groups) - 1
            return current[target.id]

        for entry in self.entries:
            if entry.kind == "moved":
                if entry.previous.id not in current:
                    open_group(entry.previous)
                index = current.pop(entry.previous.id)
                current[entry.target.id] = index
                groups[index][0] = entry.target
            elif entry.target.id not in current:  # Added again while live continues the same history.
                index = open_group(entry.target)
            else:
                index = current[entry.target.id]
            groups[index][1].append(entry)
            if entry.kind == "removed":
                groups[index][2] = True
                current.pop(entry.target.id, None)
        return [Lineage(target, tuple(entries), removed, origin) for target, entries, removed, origin in groups]

    def ids(self) -> dict[ResourceTarget, str]:
        """The lineage ID of every live target: its ID when first recorded, kept through moves."""
        return {lineage.target: lineage.id for lineage in self.lineages() if not lineage.removed}

    def history(self, target: ResourceTarget) -> tuple[Entry, ...]:
        """Entries of the live target at this address, including those recorded under earlier addresses."""
        for lineage in reversed(self.lineages()):
            if lineage.target == target and not lineage.removed:
                return lineage.entries
        return ()

    def changes(self, target: ResourceTarget | None = None, *, subsections: bool = False) -> list[Change]:
        """Change entries, oldest first: of everything, of one live target across its moves, or with
        ``subsections`` of the target and every section under it (removed ones included)."""
        if target is None:
            entries: Sequence[Entry] = self.entries
        elif subsections:
            entries = sorted(
                (entry for lineage in self.lineages() if target.contains(lineage.target) for entry in lineage.entries),
                key=lambda entry: entry.id,
            )
        else:
            entries = self.history(target)
        return [entry for entry in entries if isinstance(entry, _Change)]

    def diff(
        self,
        commit: str | None = None,
        head: str = "HEAD",
        *,
        under: ResourceTarget | None = None,
        context_lines: int = 3,
    ) -> str:
        """Net change of every section between two recorded commits, from the journal alone (no repository).

        Covers the change entries recorded after ``commit`` (exclusive; ``None`` = from the
        start) up to ``head`` (inclusive; ``"HEAD"`` = everything recorded). Commits may be
        abbreviated. Each changed section appears once, under its latest address, with the
        reasons of the commits that changed it and a diff from its first to its last text.
        ``under`` limits the diff to a resource or section and everything under it.
        """
        start = self._position(commit) + 1 if commit is not None else 0
        end = len(self.entries) if head == "HEAD" else self._position(head) + 1
        lineages = self.lineages()
        lineage_of = {entry.id: index for index, lineage in enumerate(lineages) for entry in lineage.entries}
        groups: dict[int, list[Change]] = {}
        for entry in self.entries[start:end]:
            if isinstance(entry, _Change) and (under is None or under.contains(lineages[lineage_of[entry.id]].target)):
                groups.setdefault(lineage_of[entry.id], []).append(entry)
        blocks = []
        for changes in groups.values():
            first, last = changes[0], changes[-1]
            if first.kind == "added" and last.kind == "removed":
                continue
            texts = [change for change in changes if change.kind != "moved"]
            old = first.previous if first.kind == "moved" else None if first.kind == "added" else first.target
            new = None if last.kind == "removed" else last.target
            header = f"=== {new or old}" + (f"  (was {old})" if old and new and old != new else "")
            reasons = [
                f"    {(change.commit or '-')[:8]} {(change.reason or '-').splitlines()[0]}" for change in changes
            ]
            body = ""
            if texts:
                before = (
                    None
                    if texts[0].kind == "added"
                    else (texts[0].before if texts[0].kind == "modified" else texts[0].content)
                )
                after = (
                    None
                    if texts[-1].kind == "removed"
                    else (texts[-1].after if texts[-1].kind == "modified" else texts[-1].content)
                )
                body = _unified(before, after, old, new, context_lines)
            blocks.append("\n".join(part for part in (header, *reasons, body) if part))
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    def _position(self, commit: str) -> int:
        """Index of the last entry at or before ``commit`` (a full or abbreviated hash).

        A commit that recorded nothing itself is found as the parent of a later one.
        """
        known = {entry.commit for entry in self.entries if entry.commit} | {
            entry.parent for entry in self.entries if isinstance(entry, _Change) and entry.parent
        }
        matches = {sha for sha in known if sha.startswith(commit)}
        if len(commit) < 4 or len(matches) != 1:
            raise ValueError(f"{commit!r} does not name exactly one commit known to the journal")
        (sha,) = matches
        recorded = [index for index, entry in enumerate(self.entries) if entry.commit == sha]
        if recorded:
            return recorded[-1]
        return (
            min(index for index, entry in enumerate(self.entries) if isinstance(entry, _Change) and entry.parent == sha)
            - 1
        )

    def recorded_commits(self) -> set[str]:
        return {entry.commit for entry in self.entries if entry.commit is not None}

    def kept(self) -> list[Kept]:
        return [entry for entry in self.entries if isinstance(entry, Kept)]

    def ratings(self, scale: str | None = None) -> list[Rated]:
        return [entry for entry in self.entries if entry.kind == "rated" and scale in {None, entry.scale}]

    def summaries(self, scale: RatingScale, policy: RatingPolicy = count_all) -> dict[ResourceTarget, Summary]:
        """Summary per live target for ``scale``; targets with no counted ratings have none."""
        result = {}
        live = {lineage.target: lineage for lineage in self.lineages() if not lineage.removed}
        # Enclosing sections may hold no entries themselves, yet a roll-up policy gives them a summary.
        for ancestor in {a for target in live for a in target.ancestors()} - live.keys():
            live[ancestor] = Lineage(ancestor, (), removed=False, id=ancestor.id)
        for lineage in live.values():
            weighted = ((rated.value, weight) for rated, weight in policy(lineage, self) if rated.scale == scale.name)
            summary = scale.summarize(weighted)
            if summary is not None:
                result[lineage.target] = summary
        return result


_ENTRY: TypeAdapter[Entry] = TypeAdapter(Entry)


def dump_journal(entries: Iterable[Entry]) -> str:
    """One compact JSON entry per line, sorted by ID."""
    return "".join(entry.model_dump_json() + "\n" for entry in sorted(entries, key=lambda entry: entry.id))


def load_journal(text: str, *, name: str = "journal.jsonl") -> list[Entry]:
    """Parse journal lines. Lines may be out of order or repeated (as after a union merge or a replayed
    append); the same ID with different content is an error, since entries are never rewritten. An
    unfinished last line — an append cut short — is skipped with a warning."""
    entries: dict[str, Entry] = {}
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            entry = _ENTRY.validate_json(line)
        except ValidationError as error:
            if number == len(lines) and not text.endswith("\n"):
                warnings.warn(f"{name}:{number}: skipping an unfinished last line", stacklevel=2)
                continue
            raise ValueError(f"{name}:{number}: invalid entry: {error}") from None
        if entry.id in entries and entries[entry.id] != entry:
            raise ValueError(f"{name}:{number}: entry {entry.id} differs from an earlier line")
        entries[entry.id] = entry
    return sorted(entries.values(), key=lambda entry: entry.id)
