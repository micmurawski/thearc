"""Evolution: a workspace's history as one JSON dataset — revisions, sections, ratings, and changes.

The dataset is what ``thearc report`` renders; it is plain JSON so other tools can use it too.
It combines the journals of every branch (ratings recorded on one, a curator's changes on
another), follows sections by **lineage ID** across renames, and ties every revision to the
commit where it first appears and to its configuration files (without markers).

    {
      "agent": {"name", "version"}, "generated_at", "head",
      "scales": [{"name", "labels", "ordered"}],
      "revisions": [{"revision", "commit", "at", "message", "branches", "files": {path: text},
                     "spans": {section id: [path, line]}}],
      "branches": [{"name", "head", "current", "commits": [{"commit", "at", "message", "revision"}]}],
      "sections": [{"id", "address", "kind", "name", "section", "depth", "path", "removed", "names"}],
      "cells": {section id: {revision: {"ratings": {scale: {value: count}}, "events": [kind, …]}}},
      "changes": [{"section", "kind", "revision", "commit", "at", "reason", "author", "diff", "branches", …}],
      "ratings": [{"section", "revision", "scale", "value", "item", "reflection", "ref", "at", "metadata"}],
      "reflections": {id: {"markdown", "revision", "at", "reflector", "evidence"}}
    }
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thearc.resources.git import GitError, _git, agent_at
from thearc.resources.journal import Entry, Journal, Kept, Modified, Moved, Rated, _unified, load_journal
from thearc.resources.ratings import CategoricalScale, RatingScale
from thearc.resources.targets import ResourceTarget
from thearc.resources.workspace import JOURNAL_FILE

# Labels people commonly use, from worst to best, to order and colour scales the dataset only sees values of.
_SENTIMENT = ["harmful", "bad", "negative", "wrong", "neutral", "mixed", "unclear", "helpful", "good", "positive"]


def evolution(
    workspace: str | Path,
    *,
    refs: Sequence[str] | None = None,
    scales: Sequence[RatingScale] = (),
    reflections: str | Path | None = None,
) -> dict[str, Any]:
    """The evolution dataset of ``workspace`` across ``refs`` (default: every local branch).

    ``scales`` give label order and meaning; without them, labels are ordered by common sentiment
    words (harmful … helpful). ``reflections`` is a folder of saved reflections
    (``Reflection.dump_items_to_json``) to include as Markdown.
    """
    root = Path(workspace).resolve()
    head = _git(root, "rev-parse", "--abbrev-ref", "HEAD").decode().strip()
    refs = list(refs) if refs is not None else _branches(root)
    entries: dict[str, Entry] = {}
    found_in: dict[str, set[str]] = {}
    for ref in refs:
        try:
            text = _git(root, "show", f"{ref}:{JOURNAL_FILE}").decode()
        except GitError:
            continue
        for entry in load_journal(text):
            entries.setdefault(entry.id, entry)
            found_in.setdefault(entry.id, set()).add(ref)
    if (root / JOURNAL_FILE).is_file():  # Lines appended since the last commit, e.g. by add_reflection.
        for entry in Journal.load(root / JOURNAL_FILE).entries:
            entries.setdefault(entry.id, entry)
            found_in.setdefault(entry.id, set()).add(head)
    journal = Journal(entries=sorted(entries.values(), key=lambda entry: entry.id))

    revisions, branches = _history(root, refs, head)
    known = {revision["revision"] for revision in revisions}
    for entry in journal.entries:  # Revisions only the journal knows (not committed yet).
        if entry.revision not in known:
            known.add(entry.revision)
            revisions.append({"revision": entry.revision, "commit": None, "at": entry.at.isoformat(),
                              "message": None, "branches": [], "files": {}, "spans": {}})

    lineages = journal.lineages()
    section_of = {entry.id: lineage.id for lineage in lineages for entry in lineage.entries}
    by_address: dict[str, str] = {}  # Every address a section ever had → its lineage ID.
    for lineage in lineages:
        for entry in lineage.entries:
            by_address[str(entry.target)] = lineage.id
            if isinstance(entry, Moved):
                by_address[str(entry.previous)] = lineage.id
    for revision in revisions:  # Where each section starts, per revision: {section ID: [path, line]}.
        revision["spans"] = {by_address[address]: place for address, place in revision.get("spans", {}).items()
                             if address in by_address}
    current = agent_at(root, head)
    order = {target: index for index, target in enumerate(current.files().targets)} if current else {}
    sections = [_section(lineage, order) for lineage in lineages]
    sections.sort(key=lambda s: (s["order"] if s["order"] is not None else len(order), s["address"]))

    cells: dict[str, dict[str, Any]] = {}
    changes, ratings = [], []
    for entry in journal.entries:
        section = section_of[entry.id]
        cell = cells.setdefault(section, {}).setdefault(entry.revision, {"ratings": {}, "events": []})
        if isinstance(entry, Rated):
            counts = cell["ratings"].setdefault(entry.scale, {})
            counts[str(entry.value)] = counts.get(str(entry.value), 0) + 1
            ratings.append({
                "section": section, "revision": entry.revision, "scale": entry.scale, "value": entry.value,
                "item": entry.item_id, "reflection": entry.extra.get("reflection"), "ref": entry.ref,
                "at": entry.at.isoformat(), "metadata": entry.metadata,
                "branches": sorted(found_in.get(entry.id, ())),
            })
            continue
        cell["events"].append(entry.kind)
        changes.append(_change(entry, section, sorted(found_in.get(entry.id, ()))))

    return {
        "agent": {"name": current.name if current else root.name, "version": current.version if current else None},
        "generated_at": datetime.now(UTC).isoformat(),
        "head": head,
        "scales": _scales(scales, ratings),
        "revisions": revisions,
        "branches": branches,
        "sections": sections,
        "cells": cells,
        "changes": changes,
        "ratings": ratings,
        "reflections": _reflections(reflections) if reflections else {},
    }


def _branches(root: Path) -> list[str]:
    names = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads").decode().split()
    return names or ["HEAD"]


def _history(root: Path, refs: list[str], head: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every revision in the order its commits were made, and each branch's commits."""
    log = _git(root, "log", "--topo-order", "--reverse", "--format=%H%x00%cI%x00%s", *refs).decode()
    revisions: dict[str, dict[str, Any]] = {}
    revision_of: dict[str, str | None] = {}
    for line in log.splitlines():
        sha, at, message = line.split("\x00", 2)
        try:
            agent = agent_at(root, sha)
        except (GitError, ValueError):
            agent = None
        revision_of[sha] = agent.revision if agent else None
        if agent is not None and agent.revision not in revisions:
            agent_files = agent.files()
            spans = {str(target): [agent_files.span(target).path, agent_files.span(target).start]
                     for target in agent_files.targets}
            revisions[agent.revision] = {"revision": agent.revision, "commit": sha, "at": at, "message": message,
                                         "branches": [], "files": dict(agent_files.files), "spans": spans}
    branches = []
    for ref in refs:
        lines = _git(root, "log", "--first-parent", "--reverse", "--format=%H%x00%cI%x00%s", ref).decode()
        commits = []
        for line in lines.splitlines():
            sha, at, message = line.split("\x00", 2)
            revision = revision_of.get(sha)
            commits.append({"commit": sha, "at": at, "message": message, "revision": revision})
            if revision in revisions and ref not in revisions[revision]["branches"]:
                revisions[revision]["branches"].append(ref)
        branches.append({"name": ref, "head": commits[-1]["commit"] if commits else None, "current": ref == head,
                         "commits": commits})
    return list(revisions.values()), branches


def _section(lineage: Any, order: dict[ResourceTarget, int]) -> dict[str, Any]:
    target: ResourceTarget = lineage.target
    path = target.section.split(" > ") if target.section else []
    names = [{"revision": entry.revision, "at": entry.at.isoformat(), "address": str(entry.target),
              "previous": str(entry.previous)} for entry in lineage.entries if isinstance(entry, Moved)]
    return {
        "id": lineage.id,
        "address": str(target),
        "kind": target.kind,
        "name": target.name,
        "section": target.section,
        "path": path,
        "depth": len(path),
        "removed": lineage.removed,
        "names": names,
        "order": order.get(target),
    }


def _change(entry: Entry, section: str, branches: list[str]) -> dict[str, Any]:
    change: dict[str, Any] = {
        "section": section, "kind": entry.kind, "revision": entry.revision, "commit": entry.commit,
        "at": entry.at.isoformat(), "address": str(entry.target), "branches": branches,
        "author": entry.extra.get("author"), "extra": entry.extra,
    }
    if isinstance(entry, Kept):
        return change | {"reason": entry.reason, "diff": ""}
    reason = getattr(entry, "reason", None)
    if isinstance(entry, Modified):
        diff = _unified(entry.before, entry.after, entry.target, entry.target)
    elif isinstance(entry, Moved):
        diff = f"moved: {entry.previous} → {entry.target}"
        change["previous"] = str(entry.previous)
    else:
        content = getattr(entry, "content", "")
        diff = _unified(None, content, None, entry.target) if entry.kind == "added" else _unified(
            content, None, entry.target, None)
    return change | {"reason": reason, "diff": diff, "touched": getattr(entry, "touched", [])}


def _scales(scales: Sequence[RatingScale], ratings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    given = {scale.name: scale for scale in scales}
    seen: dict[str, list[str]] = {}
    for rating in ratings:
        values = seen.setdefault(rating["scale"], [])
        if str(rating["value"]) not in values:
            values.append(str(rating["value"]))
    result = []
    for name in sorted(set(seen) | set(given)):
        scale = given.get(name)
        if isinstance(scale, CategoricalScale):
            result.append({"name": name, "labels": list(scale.labels), "ordered": scale.ordered,
                           "meanings": scale.meanings})
            continue
        values = seen.get(name, [])
        numeric = all(_is_number(value) for value in values) and values
        ranked = sorted(values, key=lambda v: (float(v),) if numeric else (
            _SENTIMENT.index(v.lower()) if v.lower() in _SENTIMENT else len(_SENTIMENT), v))
        result.append({"name": name, "labels": ranked, "ordered": bool(numeric) or all(
            v.lower() in _SENTIMENT for v in values), "numeric": bool(numeric), "meanings": {}})
    return result


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _reflections(folder: str | Path) -> dict[str, Any]:
    from thearc.reflection.markdown import to_markdown

    result = {}
    for path in sorted(Path(folder).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "id" not in data:
            continue
        output = data.get("output", data.get("items"))
        result[data["id"]] = {
            "markdown": to_markdown(output, level=3) if output is not None else "",
            "revision": data.get("revision"), "at": data.get("at"),
            "reflector": data.get("reflector", {}), "evidence": data.get("evidence", {}), "file": path.name,
        }
    return result
