"""The journal: a MetaAgent's append-only history of targets and ratings."""

from __future__ import annotations

import difflib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictFloat, StrictStr, field_validator

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


def _unified(before: str | None, after: str | None, old: ResourceTarget | None, new: ResourceTarget | None,
             context_lines: int = 3) -> str:
    lines = difflib.unified_diff([] if before is None else before.splitlines(),
                                 [] if after is None else after.splitlines(),
                                 fromfile=f"a/{old}" if old else "/dev/null", tofile=f"b/{new}" if new else "/dev/null",
                                 lineterm="", n=context_lines)
    return "\n".join(lines)


class Rated(_Entry):
    """One validated reflection item, resolved to one target."""

    kind: Literal["rated"] = "rated"
    scale: str
    value: StrictStr | StrictFloat
    ref: str
    item_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


Entry = Annotated[Added | Modified | Moved | Removed | Rated, Field(discriminator="kind")]


@dataclass(frozen=True)
class Lineage:
    """One target's history across moves, oldest entry first."""

    target: ResourceTarget
    entries: tuple[Entry, ...]
    removed: bool


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

    def __len__(self) -> int:
        return len(self.entries)

    def lineages(self) -> list[Lineage]:
        """Group entries by target, following Moved entries. A target added again after removal starts anew."""
        current: dict[str, int] = {}
        groups: list[list[Any]] = []

        def open_group(target: ResourceTarget) -> int:
            groups.append([target, [], False])
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
        return [Lineage(target, tuple(entries), removed) for target, entries, removed in groups]

    def history(self, target: ResourceTarget) -> tuple[Entry, ...]:
        """Entries of the live target at this address, including those recorded under earlier addresses."""
        for lineage in reversed(self.lineages()):
            if lineage.target == target and not lineage.removed:
                return lineage.entries
        return ()

    def changes(self, target: ResourceTarget | None = None) -> list[Change]:
        """Change entries, oldest first: of one live target across its moves, or of everything."""
        entries = self.history(target) if target is not None else self.entries
        return [entry for entry in entries if entry.kind != "rated"]

    def diff(self, commit: str | None = None, head: str = "HEAD", *, context_lines: int = 3) -> str:
        """Net change of every section between two recorded commits, from the journal alone (no repository).

        Covers the change entries recorded after ``commit`` (exclusive; ``None`` = from the
        start) up to ``head`` (inclusive; ``"HEAD"`` = everything recorded). Commits may be
        abbreviated. Each changed section appears once, under its latest address, with the
        reasons of the commits that changed it and a diff from its first to its last text.
        """
        start = self._position(commit) + 1 if commit is not None else 0
        end = len(self.entries) if head == "HEAD" else self._position(head) + 1
        lineage_of = {entry.id: index for index, lineage in enumerate(self.lineages()) for entry in lineage.entries}
        groups: dict[int, list[Change]] = {}
        for entry in self.entries[start:end]:
            if entry.kind != "rated":
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
            reasons = [f"    {(change.commit or '-')[:8]} {(change.reason or '-').splitlines()[0]}"
                       for change in changes]
            body = ""
            if texts:
                before = None if texts[0].kind == "added" else (
                    texts[0].before if texts[0].kind == "modified" else texts[0].content)
                after = None if texts[-1].kind == "removed" else (
                    texts[-1].after if texts[-1].kind == "modified" else texts[-1].content)
                body = _unified(before, after, old, new, context_lines)
            blocks.append("\n".join(part for part in (header, *reasons, body) if part))
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    def _position(self, commit: str) -> int:
        """Index of the last entry at or before ``commit`` (a full or abbreviated hash).

        A commit that recorded nothing itself is found as the parent of a later one.
        """
        known = {entry.commit for entry in self.entries if entry.commit} | {
            entry.parent for entry in self.entries if entry.kind != "rated" and entry.parent}
        matches = {sha for sha in known if sha.startswith(commit)}
        if len(commit) < 4 or len(matches) != 1:
            raise ValueError(f"{commit!r} does not name exactly one commit known to the journal")
        (sha,) = matches
        recorded = [index for index, entry in enumerate(self.entries) if entry.commit == sha]
        if recorded:
            return recorded[-1]
        return min(index for index, entry in enumerate(self.entries)
                   if entry.kind != "rated" and entry.parent == sha) - 1

    def recorded_commits(self) -> set[str]:
        return {entry.commit for entry in self.entries if entry.commit is not None}

    def ratings(self, scale: str | None = None) -> list[Rated]:
        return [entry for entry in self.entries if entry.kind == "rated" and scale in {None, entry.scale}]

    def summaries(self, scale: RatingScale, policy: RatingPolicy = count_all) -> dict[ResourceTarget, Summary]:
        """Summary per live target for ``scale``; targets with no counted ratings have none."""
        result = {}
        for lineage in self.lineages():
            if lineage.removed:
                continue
            weighted = ((rated.value, weight) for rated, weight in policy(lineage.entries) if rated.scale == scale.name)
            summary = scale.summarize(weighted)
            if summary is not None:
                result[lineage.target] = summary
        return result
