"""The evolution graph: how a workspace's candidates branch from a base, merge, and become releases.

Built from branch names and the journal, for the report's Evolution graph:

    candidate/<run>/<name>   a pipeline's candidate (its worktree's branch); runs group by <run>
    curation/<id>            a curation outside a worktree, unless a candidate already holds it
    merge/<id>               a merged candidate; its entries name the candidates (``extra.candidates``),
                             else the commit messages do
    release/<version>        a release; it came from the closest merge below it

    {"trunk": branch, "bases": [{"commit", "version", "revision", "at"}],
     "runs": [{"id", "label", "at", "base", "base_version", "stale_base",
               "candidates": [{"branch", "name", "head", "commits", "ratings", "reflections", "merged_into"}],
               "merges": [{"branch", "head", "commits", "candidates", "release"}],
               "sections": {section id: {"base": text, "heads": {branch: text}}}}],
     "releases": [{"branch", "version", "commit", "at", "merge", "parent_version"}]}

A commit is listed when the journal records changes for it: ``{"commit", "at", "author", "message",
"revision", "sections", "kinds"}``. A candidate no merge took is a dead branch.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thearc.resources.git import GitError, _git, agent_at, snapshot_at
from thearc.resources.targets import ResourceTarget

KINDS = ("candidate/", "merge/", "release/", "curation/")
_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$")


def evolution_graph(
    root: Path, refs: list[str], head: str, changes: list[dict[str, Any]], ratings: list[dict[str, Any]],
    targets: dict[str, list[ResourceTarget]],
) -> dict[str, Any]:
    """The evolution graph of ``refs`` in the workspace at ``root``; ``changes``/``ratings`` as in the evolution
    dataset, ``targets`` every address each section (lineage ID) has had, latest first."""
    trunk = head if not head.startswith(KINDS) else ("main" if "main" in refs else head)
    tips = {ref: _rev(root, ref) for ref in refs}
    by_commit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for change in changes:
        if change.get("commit"):
            by_commit[change["commit"]].append(change)
    versions: dict[str, Any] = {}

    def version_at(sha: str) -> str | None:
        if sha not in versions:
            try:
                agent = agent_at(root, sha)
            except (GitError, ValueError):
                agent = None
            versions[sha] = agent.version if agent else None
        return versions[sha]

    depths: dict[str, int] = {}

    def depth(sha: str) -> int:
        if sha not in depths:
            depths[sha] = int(_git(root, "rev-list", "--count", sha).decode())
        return depths[sha]

    def base_of(ref: str) -> str:
        """Where ``ref`` forked: its nearest merge-base with a branch that does not contain it. Not just the
        trunk's: once the trunk moves past a released merge, the trunk contains the whole merge."""
        if ref == trunk:
            return _git(root, "rev-list", "--max-parents=0", tips[ref]).decode().split()[0]
        if ref.startswith("candidate/") and any(tip == tips[ref] for other, tip in tips.items()
                                                if other != ref and not other.startswith("curation/")):
            return tips[ref]  # A candidate with no commits yet, on the release (or other branch) it forked from.
        forks = {fork for other, tip in tips.items()
                 if other != ref and not other.startswith("curation/") and not _contains(root, tip, tips[ref])
                 and (fork := _merge_base(root, tip, tips[ref]))}
        if not forks:
            return _git(root, "rev-list", "--max-parents=0", tips[ref]).decode().split()[0]
        return max(forks, key=depth)

    def time_of(sha: str) -> str:
        return _utc(_git(root, "log", "-1", "--format=%cI", sha).decode().strip())

    def commits(base: str, ref: str) -> list[dict[str, Any]]:
        log = _git(root, "log", "--reverse", "--first-parent", "--format=%H%x00%cI%x00%an%x00%s",
                   f"{base}..{tips[ref]}").decode()
        result = []
        for line in log.splitlines():
            sha, at, author, subject = line.split("\x00", 3)
            at = _utc(at)
            found = by_commit.get(sha)
            if not found:
                continue
            result.append({"commit": sha, "at": at, "author": author, "message": found[0].get("reason") or subject,
                           "revision": found[0]["revision"], "sections": sorted({c["section"] for c in found}),
                           "kinds": sorted({c["kind"] for c in found})})
        return result

    candidates = [ref for ref in refs if ref.startswith("candidate/")]
    candidates += [ref for ref in refs if ref.startswith("curation/")
                   and not any(_contains(root, tips[c], tips[ref]) for c in candidates)]
    runs: dict[str, dict[str, Any]] = {}

    def run_for(group: str, base: str) -> dict[str, Any]:
        key = f"{group}@{base}"
        if key not in runs:
            runs[key] = {"id": group, "label": _label(group), "at": None, "base": base,
                         "base_version": version_at(base), "stale_base": None, "candidates": [], "merges": [],
                         "sections": {}}
        return runs[key]

    by_branch: dict[str, dict[str, Any]] = {}
    group_of: dict[str, str] = {}
    base_of_branch: dict[str, str] = {}
    for ref in candidates:
        base_of_branch[ref] = base = base_of(ref)
        group_of[ref], name = _group(ref)
        since = datetime.fromisoformat(time_of(base))  # Its own: recorded after it forked, merged on or not.
        own = [r for r in ratings if ref in r.get("branches", []) and datetime.fromisoformat(r["at"]) >= since]
        reflections = {r.get("reflection") for r in own if r.get("reflection")}
        by_branch[ref] = {"branch": ref, "name": name, "head": tips[ref], "commits": commits(base, ref),
                          "ratings": len(own), "reflections": len(reflections), "merged_into": []}

    release_refs = [ref for ref in refs if ref.startswith("release/")]
    merges: list[tuple[str, dict[str, Any]]] = []
    for ref in [r for r in refs if r.startswith("merge/")]:
        base = base_of(ref)
        listed = commits(base, ref)
        named: list[str] = []
        for item in listed:
            for change in by_commit[item["commit"]]:
                named += [c for c in change.get("extra", {}).get("candidates", []) if c in by_branch]
        if not named:  # Merges recorded before entries named their candidates: the messages do.
            text = " ".join(item["message"] for item in listed)
            named = mentioned(list(by_branch), text)
        named = list(dict.fromkeys(named))
        merge: dict[str, Any] = {"branch": ref, "head": tips[ref], "commits": listed, "candidates": named,
                                 "release": None}
        merges.append((base, merge))
        for branch in named:
            by_branch[branch]["merged_into"].append(ref)
            old, new = group_of[branch], group_of[named[0]]
            group_of.update({b: new for b, g in group_of.items() if g == old})  # Merged together: one run.

    for ref in candidates:
        run_for(group_of[ref], base_of_branch[ref])["candidates"].append(by_branch[ref])
    for base, merge in merges:
        group = group_of[merge["candidates"][0]] if merge["candidates"] else merge["branch"]
        run_for(group, base)["merges"].append(merge)

    # A release came from the merge closest below it: once the trunk moves on, a later release contains every
    # earlier merge too, so the one with the fewest commits between them is its source.
    releases: list[dict[str, Any]] = []
    for ref in release_refs:
        below = [(int(_git(root, "rev-list", "--count", f"{merge['head']}..{tips[ref]}").decode()), merge)
                 for _, merge in merges if _contains(root, tips[ref], merge["head"])]
        source = min(below, key=lambda item: item[0])[1] if below else None
        release = {"branch": ref, "version": version_at(tips[ref]), "commit": tips[ref], "at": time_of(tips[ref]),
                   "merge": source["branch"] if source else None, "parent_version": version_at(base_of(ref))}
        if source is not None and source["release"] is None:
            source["release"] = {"branch": ref, "version": release["version"], "commit": tips[ref]}
        releases.append(release)
    releases.sort(key=lambda r: r["at"])

    for run in runs.values():
        times = [c["at"] for cand in run["candidates"] for c in cand["commits"]]
        times += [c["at"] for m in run["merges"] for c in m["commits"]]
        run["at"] = min(times) if times else time_of(run["base"])
        # Stale: a release existed when the run started, yet the run's base does not contain it.
        missed = [r for r in releases if r["at"] <= run["at"] and not _contains(root, run["base"], r["commit"])]
        if missed:
            run["stale_base"] = missed[-1]["version"]
        heads = {c["branch"]: c["head"] for c in run["candidates"]} | {m["branch"]: m["head"] for m in run["merges"]}
        changed = {s for c in run["candidates"] for item in c["commits"] for s in item["sections"]}
        changed |= {s for m in run["merges"] for item in m["commits"] for s in item["sections"]}
        run["sections"] = _texts(root, run["base"], heads, changed, targets)

    ordered = sorted(runs.values(), key=lambda r: r["at"])
    bases: list[dict[str, Any]] = []
    for run in ordered:
        if run["base"] not in [b["commit"] for b in bases]:
            bases.append({"commit": run["base"], "version": run["base_version"], "at": time_of(run["base"])})
    return {"trunk": trunk, "bases": bases, "runs": ordered, "releases": releases}


def mentioned(branches: list[str], text: str) -> list[str]:
    """The branches ``text`` names in full: ``candidate/c1`` is not named by ``candidate/c10``."""
    return [branch for branch in branches if re.search(rf"(?<![\w/.-]){re.escape(branch)}(?![\w/.-])", text)]


def _utc(at: str) -> str:
    """An ISO commit time in UTC, so times from committers in different zones compare as text."""
    return datetime.fromisoformat(at).astimezone(UTC).isoformat()


def _rev(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", f"{ref}^{{commit}}").decode().strip()


def _merge_base(root: Path, one: str, two: str) -> str | None:
    try:
        return _git(root, "merge-base", one, two).decode().strip() or None
    except GitError:
        return None  # Unrelated histories.


def _contains(root: Path, tip: str, commit: str) -> bool:
    """Whether ``commit`` is ``tip`` or one of its ancestors."""
    try:
        _git(root, "merge-base", "--is-ancestor", commit, tip)
    except GitError:
        return False
    return True


def _group(branch: str) -> tuple[str, str]:
    """(run, candidate name): ``candidate/<run>/<name>`` → (run, name); anything else is its own run."""
    parts = branch.split("/")
    if parts[0] == "candidate" and len(parts) >= 3:
        return "/".join(parts[1:-1]), parts[-1]
    return branch, parts[-1]


def _label(group: str) -> str:
    match = _STAMP.match(group)
    if not match:
        return group
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    when = datetime(year, month, day, hour, minute, second)  # noqa: DTZ001 -- a local run stamp, shown as written
    return when.strftime("%-d %b %H:%M")


def _texts(root: Path, base: str, heads: dict[str, str], sections: set[str],
           targets: dict[str, list[ResourceTarget]]) -> dict[str, dict[str, Any]]:
    """Each section's text at the base and at every head: found by its lineage ID in the markers there, or —
    for a target without a marker line, like a whole skill — by the addresses the journal knows."""
    def at(sha: str) -> dict[str, str]:
        snapshot = snapshot_at(root, sha)
        if snapshot is None:
            return {}
        agent, ids = snapshot
        files = agent.files()
        found = {}
        for target, identifier in ids.items():
            if identifier in sections:
                try:
                    found[identifier] = files.owned_text(target)
                except KeyError:
                    continue
        known = set(files.targets)
        for section in sections - set(found):
            address = next((t for t in targets.get(section, []) if t in known), None)
            if address is not None:
                found[section] = files.owned_text(address)
        return found

    base_texts = at(base)
    head_texts = {branch: at(sha) for branch, sha in heads.items()}
    return {section: {"base": base_texts.get(section),
                      "heads": {branch: texts.get(section) for branch, texts in head_texts.items()}}
            for section in sorted(sections)}
