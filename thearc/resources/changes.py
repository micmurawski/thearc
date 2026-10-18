"""Reconcile two revisions of a MetaAgent into Added / Modified / Moved / Removed journal entries.

A move is recorded only when it is unambiguous: a target whose workspace marker
(its lineage ID) is now on another address — certain, whatever else changed —
or, without markers, a resource whose content, apart from its identity, is
unchanged under exactly one new address, or a section or skill file whose owned
content is non-empty and matches exactly one new address. Anything else is
recorded as Removed plus Added.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from thearc.resources.agent import MetaAgent
from thearc.resources.files import AgentFiles
from thearc.resources.journal import Added, Entry, Modified, Moved, Removed
from thearc.resources.targets import ResourceTarget


def _unique_pairs(
    removed: Iterable[ResourceTarget], added: Iterable[ResourceTarget], key: Callable[[ResourceTarget], Hashable | None]
) -> list[tuple[ResourceTarget, ResourceTarget]]:
    groups: dict[Hashable, tuple[list, list]] = defaultdict(lambda: ([], []))
    for side, targets in ((0, removed), (1, added)):
        for target in targets:
            value = key(target)
            if value is not None:
                groups[value][side].append(target)
    return [(old[0], new[0]) for old, new in groups.values() if len(old) == 1 and len(new) == 1]


def _renamed(target: ResourceTarget, old: ResourceTarget, new: ResourceTarget) -> ResourceTarget | None:
    """The address ``target`` has after resource ``old`` became ``new``, if it belongs to ``old``."""
    if (target.kind, target.name) == (old.kind, old.name):
        return target.model_copy(update={"name": new.name})
    if old.kind == "skill" and target.kind == "skill_file" and target.name.startswith(old.name + "/"):
        return target.model_copy(update={"name": new.name + target.name[len(old.name) :]})
    return None


def detect_changes(
    before: MetaAgent | None,
    after: MetaAgent,
    *,
    reason: str | None = None,
    at: datetime | None = None,
    commit: str | None = None,
    parent: str | None = None,
    identities: tuple[Mapping[ResourceTarget, str], Mapping[ResourceTarget, str]] | None = None,
    **extra: Any,
) -> list[Entry]:
    """Entries describing how ``before`` became ``after``; ``before=None`` records every target as Added.

    Each changed target gets its own entry, all sharing ``reason``. Keyword
    arguments are stored on every entry as ``extra``. ``identities`` are the lineage IDs the
    workspace markers gave on each side: a section that kept its ID under a new address moved.
    """
    at = at or datetime.now(UTC)
    new_files = after.files()
    old_files = before.files() if before is not None else None
    new_targets = set(new_files.targets)
    old_targets = set(old_files.targets) if old_files is not None else set()
    common: dict[str, Any] = {
        "revision": after.revision,
        "at": at,
        "commit": commit,
        "extra": extra,
        "reason": reason,
        "parent": parent,
    }
    removed, added = old_targets - new_targets, new_targets - old_targets
    moves: list[tuple[ResourceTarget, ResourceTarget]] = []
    marked: list[tuple[ResourceTarget, ResourceTarget]] = []  # Moves known from markers; their text may differ.

    def take(pairs: list[tuple[ResourceTarget, ResourceTarget]]) -> None:
        for old, new in pairs:
            if old in removed and new in added:
                moves.append((old, new))
                removed.discard(old)
                added.discard(new)

    if before is not None and old_files is not None:
        # Markers name the same section on both sides, so a rename is certain even if the text changed too.
        old_ids, new_ids = identities or ({}, {})
        by_id = {identifier: target for target, identifier in old_ids.items()}
        take([(by_id[identifier], new) for new, identifier in new_ids.items() if identifier in by_id])
        marked = list(moves)

        def fingerprint(agent: MetaAgent, target: ResourceTarget) -> Hashable | None:
            if target.section is not None or target.kind == "skill_file":
                return None
            resource = agent.resource(target)
            identity = {"name", "filename", "path"}
            content = resource if isinstance(resource, str) else resource.model_dump(mode="json", exclude=identity)
            return target.kind, json.dumps(content, sort_keys=True, ensure_ascii=False)

        def owned(
            files: AgentFiles, target: ResourceTarget, scope: Callable[[ResourceTarget], Hashable]
        ) -> Hashable | None:
            text = files.owned_text(target)
            return (scope(target), files.content_hash(target)) if text else None

        resource_moves = _unique_pairs(
            [t for t in removed if fingerprint(before, t) is not None],
            [t for t in added if fingerprint(after, t) is not None],
            lambda t: fingerprint(before if t in old_targets else after, t),
        )
        take(resource_moves)
        for old_resource, new_resource in resource_moves:  # Sections and skill files follow their resource.
            take(
                [
                    (t, renamed)
                    for t in list(removed)
                    if (renamed := _renamed(t, old_resource, new_resource)) is not None
                ]
            )

        def files_of(target: ResourceTarget) -> AgentFiles:
            return old_files if target in old_targets else new_files

        take(
            _unique_pairs(
                [t for t in removed if t.kind == "skill_file" and t.section is None],
                [t for t in added if t.kind == "skill_file" and t.section is None],
                lambda t: owned(files_of(t), t, lambda t: t.name.partition("/")[0]),
            )
        )
        take(
            _unique_pairs(
                [t for t in removed if t.section is not None],
                [t for t in added if t.section is not None],
                lambda t: owned(files_of(t), t, lambda t: (t.kind, t.name)),
            )
        )

    entries: list[Entry] = []
    entries += [Moved(target=new, previous=old, **common) for old, new in sorted(moves, key=lambda p: str(p[1]))]
    entries += (
        [
            Removed(target=t, content_hash=old_files.content_hash(t), content=old_files.owned_text(t), **common)
            for t in sorted(removed, key=str)
        ]
        if old_files is not None
        else []
    )
    entries += [
        Added(target=t, content_hash=new_files.content_hash(t), content=new_files.owned_text(t), **common)
        for t in sorted(added, key=str)
    ]
    if old_files is not None:
        # Unchanged addresses, and moved targets at their new address (a marker keeps a rename that also edited).
        pairs = [(target, target) for target in sorted(old_targets & new_targets, key=str)]
        pairs += sorted(marked, key=lambda pair: str(pair[1]))
        for old, new in pairs:
            before_hash, after_hash = old_files.content_hash(old), new_files.content_hash(new)
            if before_hash != after_hash:
                entries.append(
                    Modified(
                        target=new,
                        before_hash=before_hash,
                        after_hash=after_hash,
                        before=old_files.owned_text(old),
                        after=new_files.owned_text(new),
                        **common,
                    )
                )
    return entries
