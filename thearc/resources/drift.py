"""Drift: what changed in a project's installed files since the workspace's configuration was installed there.

A person edits an installed skill by hand, or another tool updates it; the next install would overwrite that.
``sync_installation`` imports what each harness has installed, works out what differs from the workspace, and
commits it there as one change, recorded in the journal with its author, so evolution builds on it instead.

Each harness installs its own copy of the configuration, so a resource is compared per copy: an edit in any
copy is taken (two copies edited differently is a conflict, and the first harness's edit is taken), and a
resource counts as removed only when every copy that should hold it lost it. A copy is only expected to hold
what its harness can install: Pi and OpenCode take no hooks or MCP servers, an extension belongs to one harness,
and a hook may be scoped to some harnesses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thearc.harnesses import Harness
from thearc.resources.agent import MetaAgent
from thearc.resources.journal import Entry

COLLECTIONS = ("skills", "context", "resources", "extensions", "hooks", "mcps")


@dataclass(frozen=True)
class Drift:
    """What ``sync_installation`` found and committed."""

    entries: tuple[Entry, ...]  # The journal entries recording the hand edits; empty when nothing drifted.
    conflicts: tuple[str, ...]  # Resources edited differently in two harnesses' copies, e.g. ``skills/retry``.
    agent: MetaAgent  # The workspace's agent afterwards.

    @property
    def changed(self) -> bool:
        return bool(self.entries)


def _expected(agent: MetaAgent, harness: str, collection: str, key: str) -> bool:
    """Whether ``harness``'s copy should hold ``agent``'s resource ``key``."""
    from thearc.resources.harness import get_harness

    layout = get_harness(harness)  # type: ignore[arg-type]
    if collection == "hooks":
        return layout.hooks_file is not None and agent.hooks[key].installs_into(harness)
    if collection == "mcps":
        return layout.mcp_file is not None
    if collection == "extensions":
        return agent.extensions[key].harness == harness
    return True


def installed_changes(agent: MetaAgent, project: str | Path,
                      harnesses: Sequence[Harness]) -> tuple[MetaAgent, list[str]]:
    """``agent`` with the edits found in its installation for ``harnesses`` in ``project``, and the conflicts."""
    imported = {harness: MetaAgent.from_installation(harness, project, name=agent.name, ignore=agent.ignore)
                for harness in harnesses}
    updates: dict[str, Any] = {}
    conflicts: list[str] = []
    for collection in COLLECTIONS:
        base: dict[str, Any] = getattr(agent, collection)
        result = dict(base)
        edited: dict[str, Any] = {}
        kept_somewhere: set[str] = set()
        for harness, copy in imported.items():
            items: dict[str, Any] = getattr(copy, collection)
            if collection == "extensions":
                items = {key: item for key, item in items.items() if item.harness == harness}
            for key, item in items.items():
                if key in base and base[key] == item:
                    kept_somewhere.add(key)
                    continue
                if key in edited and edited[key] != item:
                    conflicts.append(f"{collection}/{key}")
                    continue
                edited[key] = result[key] = item
            kept_somewhere |= {key for key in base
                               if key not in items and not _expected(agent, harness, collection, key)}
        for key in base:
            if key not in edited and key not in kept_somewhere:
                del result[key]  # Every copy that should hold it lost it.
        updates[collection] = result
    return agent.model_copy(update=updates), sorted(set(conflicts))


def sync_installation(agent: MetaAgent, project: str | Path, harnesses: Sequence[Harness], *,
                      author: str = "user", message: str | None = None) -> Drift:
    """Commit the edits made to ``agent``'s installation in ``project`` since it was installed (see the module)."""
    from thearc.resources.git import commit_configuration
    from thearc.resources.workspace import read_workspace

    root = agent.checked_workspace("sync_installation")
    updated, conflicts = installed_changes(agent, project, harnesses)
    updated._workspace = root
    reason = message or f"Edited by hand in {Path(project).resolve().name} since {agent.name} {agent.version}"
    entries = commit_configuration(updated, reason, author=author) if updated != agent else []
    return Drift(tuple(entries), tuple(conflicts), read_workspace(root))
