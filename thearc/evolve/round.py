"""A round of ``/evolve``: what a recipe is given (``Evolution``), and the commands around it.

    run      drift → the recipe's ``propose(evolution)`` → a proposal: a new version on ``proposal/<version>``,
             checked out in the workspace, with a report and a diff; nothing installed
    diff     the proposal against the installed release, with who changed what
    amend    commit the edits made in the workspace to the proposal, as the user's
    apply    release the proposal, move the workspace's ``main`` to it, install it, and push it (``--push``)
    discard  drop the proposal
    ignore   leave targets out of the configuration (or stop) — see ``MetaAgent.ignore_targets``
    rollback install an earlier release again, as a new version

A project evolves one harness's configuration (``project_harness``). Its workspace is a repository of its own, a git
submodule of the project at ``.thearc/workspace``; everything local lives under ``.thearc/cache`` (rounds, the session
index).
"""

from __future__ import annotations

import asyncio
import fcntl
import importlib.util
import json
import logging
import re
import shutil
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from thearc.curation.curator import CurationTools
from thearc.harnesses import Harness
from thearc.reflection import Reflection
from thearc.resources import MetaAgent, bump_version
from thearc.resources.git import GitError, _git, agent_at, branch_name, commit, commit_journal, record_commits
from thearc.resources.journal import Journal, load_journal
from thearc.resources.reflection import ReflectionSchema
from thearc.resources.workspace import JOURNAL_FILE
from thearc.sessions import Session, Sessions

from .defaults import SCHEMA
from .setup import CACHE, WORKSPACE, project_harness, project_root, recipe_folder, remote_of

SESSION_LIMIT = 20
EVOLVE_COMMAND = "thearc evolve"


class Batch(list[Session]):
    """The sessions a round reflects on, newest first."""


class Evolution:
    """What a recipe's ``propose(evolution)`` is given: the project's sessions, a candidate per harness, and where a
    stopped round left off. Made by ``run``."""

    def __init__(self, project: Path, release: MetaAgent, round_id: str, folder: Path, sessions: Sessions,
                 harnesses: Sequence[str], since: datetime | None, *, progress: Path | None = None):
        self.project, self.release, self.round, self.folder_path = project, release, round_id, folder
        self._sessions, self.harnesses, self._since = sessions, list(harnesses), since
        self.reason: str | None = None
        self.found = 0  # How many sessions the last ``sessions`` call found.
        self.progress = progress or folder / "progress.html"  # The live page ``track`` writes.
        self.log_file = folder / "round.log"
        # round.log, a tab on the progress page. Its own logger: two rounds may share an ID (stamped to the second).
        self.log = logging.getLogger(f"thearc.evolve.round.{round_id}.{id(self):x}")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        handler = logging.FileHandler(self.log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        self.log.addHandler(handler)

    def track(self, flow: Any, shared: dict[str, Any], *, title: str | None = None,
              logs: dict[str, str | Path] | None = None) -> dict[str, Any]:
        """Run ``flow`` with ``shared``, showing it live on the progress page (``self.progress``): its graph, each
        node's status, and the round's log with any other ``logs`` as tabs. Returns ``shared``."""
        from thearc.flow.viz import FlowTracker

        title = title or f"/evolve round {self.round} — {self.release.name} {self.release.version}"
        tracker = FlowTracker(flow, output=str(self.progress), title=title,
                              logs={"round": self.log_file, **(logs or {})})
        if hasattr(flow, "run_async"):
            asyncio.run(tracker.run_async(shared))
        else:
            tracker.run(shared)
        return shared

    def close(self) -> None:
        """Close the round's log, and forget its logger."""
        for handler in list(self.log.handlers):
            handler.close()
            self.log.removeHandler(handler)
        logging.Logger.manager.loggerDict.pop(self.log.name, None)

    def sessions(self, *, harness: str | None = None, since: timedelta | datetime | None = None,
                 limit: int = SESSION_LIMIT) -> Batch:
        """The sessions of ``harness`` (default: the harness the project evolves) run in the project with the installed
        release, newest first: started after ``since`` (an age or a time) and after the release was installed, never
        one that ran ``/evolve``. At most ``limit``."""
        harnesses = [harness] if harness is not None else self.harnesses
        cutoff = datetime.now(UTC) - since if isinstance(since, timedelta) else since
        cutoffs = [moment for moment in (cutoff, self._since) if moment is not None]
        for name in harnesses:
            self._sessions.add_harness_store(name, self.project)  # type: ignore[arg-type]
        found = self._sessions.list(harness=harnesses, cwd=self.project, since=max(cutoffs, default=None),
                                    without_call=EVOLVE_COMMAND)
        self.found = len(found)
        return Batch(self._sessions.get(meta.id) for meta in found[:limit])

    def candidate(self, name: str = "candidate") -> MetaAgent:
        """A candidate: a worktree of the release on ``thearc/candidate/<round>/<name>``; the one a stopped round made,
        if there is one. A recipe that curates several in parallel names each, and merges them."""
        folder = self.folder("candidates") / name
        if folder.exists():
            return MetaAgent.from_workspace(folder)
        root = self.release.workspace
        assert root is not None
        return self.release.to_worktree(folder, branch=branch_name(root, "candidate", f"{self.round}/{name}"))

    def reflected(self, session: Session, candidate: MetaAgent) -> bool:
        """Whether ``candidate``'s journal already holds a reflection on ``session`` (a stopped round's)."""
        return any(kept.session == session.id and kept.revision == candidate.revision
                   for kept in candidate.reload().journal.reflections)

    def reflections(self, candidate: MetaAgent, schema: ReflectionSchema = SCHEMA) -> list[Reflection]:
        """The reflections on sessions that ``candidate``'s journal holds for its revision, read with ``schema``."""
        journal = candidate.reload().journal
        return [Reflection.from_dict(kept.model_dump(mode="json"), schema) for kept in journal.reflections
                if kept.revision == candidate.revision and kept.session is not None]

    def curated(self, candidate: MetaAgent) -> bool:
        """Whether a stopped round already curated ``candidate``."""
        root = candidate.workspace
        return root is not None and _git(root, "log", "-1", "--format=%s").decode().startswith("Record curation")

    def merged(self) -> MetaAgent | None:
        """The merged candidate a stopped round made, if there is one."""
        folder = self.folder("merged")
        return MetaAgent.from_workspace(folder) if (folder / ".git").exists() else None

    def provenance(self, session: Session) -> str:
        """What the reflector should know about how ``session`` ran: which configuration was installed for it."""
        return (f"The session ran in this project with {self.release.name} {self.release.version} installed for "
                f"{session.metadata.harness}: the files you are given, which the harness loaded when the session "
                "started.")

    def folder(self, name: str) -> Path:
        """A path in this round's folder (not created)."""
        return self.folder_path / name

    def nothing(self, reason: str) -> None:
        """Say why the round proposes nothing (``propose`` then returns None)."""
        self.reason = reason


# --- results ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    target: str
    kind: str
    reason: str | None
    author: str | None


@dataclass(frozen=True)
class Proposal:
    version: str | None  # None when the round proposes nothing.
    base: str | None
    reason: str | None = None
    changes: tuple[Change, ...] = ()
    diff: str = ""
    report: str | None = None
    drift: tuple[str, ...] = ()  # Hand edits committed before the round.
    conflicts: tuple[str, ...] = ()
    sessions: int = 0
    progress: str | None = None  # The round's flow, as it ran.

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "base": self.base, "reason": self.reason,
                "changes": [change.__dict__ for change in self.changes], "diff": self.diff, "report": self.report,
                "drift": list(self.drift), "conflicts": list(self.conflicts), "sessions": self.sessions,
                "progress": self.progress}


@dataclass(frozen=True)
class Applied:
    version: str
    harnesses: tuple[str, ...]
    installed: tuple[str, ...] = field(default=())
    pushed: str | None = None  # The remote the workspace was pushed to.

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "harnesses": list(self.harnesses), "installed": list(self.installed),
                "pushed": self.pushed}


# --- the workspace ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Place:
    """Where one harness's evolution lives in a project."""

    root: Path
    harness: Harness
    workspace: Path
    cache: Path


def _place(project: str | Path) -> Place:
    harness = project_harness(project)
    root = project_root(project)
    return Place(root, harness, root / WORKSPACE, root / CACHE)


def _current_branch(workspace: Path) -> str:
    return _git(workspace, "rev-parse", "--abbrev-ref", "HEAD").decode().strip()


def _proposal_version(workspace: Path) -> str | None:
    """The version of the open proposal, if the workspace is on one."""
    prefix = branch_name(workspace, "proposal", "")
    branch = _current_branch(workspace)
    return branch.removeprefix(prefix) if branch.startswith(prefix) else None


def _main(workspace: Path) -> str:
    return branch_name(workspace, "main", "").rstrip("/")


def _pending(workspace: Path) -> list[str]:
    return [line for line in _git(workspace, "status", "--porcelain").decode().splitlines() if line.strip()]


@contextmanager
def _locked(cache: Path) -> Iterator[None]:
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "evolve.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another `thearc evolve` is running in this project") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _no_proposal(workspace: Path) -> None:
    if (version := _proposal_version(workspace)) is not None:
        raise ValueError(f"proposal {version} is open: apply or discard it first")


def _behind_remote(workspace: Path) -> bool:
    main = _main(workspace)
    try:
        remote = _git(workspace, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{main}").decode().strip()
    except GitError:
        return False
    try:
        _git(workspace, "merge-base", "--is-ancestor", remote, main)
    except GitError:
        return True
    return False


def _installed_at(workspace: Path, release: MetaAgent) -> datetime | None:
    """When ``release`` was released (its branch's commit), or None for the adopted first version."""
    try:
        at = _git(workspace, "log", "-1", "--format=%cI", branch_name(workspace, "release", release.version or "")
                  ).decode().strip()
    except GitError:
        return None
    return datetime.fromisoformat(at) if at else None


def load_recipe(path: Path) -> ModuleType:
    """The recipe at ``path`` (``evolve.py``), imported; it reads its prompts from next to itself."""
    name = f"thearc_recipe_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None or not path.is_file():
        raise ValueError(f"no recipe at {path}; reinstall the evolve skill")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # No __pycache__ in the installed skill.
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes
    if not callable(getattr(module, "propose", None)):
        raise TypeError(f"the recipe {path} must define propose(evolution)")
    return module


def _changes(workspace: Path, base: str, head: str) -> tuple[Change, ...]:
    """The changes recorded between ``base`` and ``head``, with who made each."""
    commits = set(_git(workspace, "rev-list", f"{base}..{head}").decode().split())
    text = _git(workspace, "show", f"{head}:{JOURNAL_FILE}").decode()
    return tuple(Change(str(entry.target), entry.kind, getattr(entry, "reason", None), entry.extra.get("author"))
                 for entry in load_journal(text) if entry.commit in commits and entry.kind != "rated")


# --- commands -----------------------------------------------------------------------------------------------------


Recipe = Callable[[Evolution], MetaAgent | None]


def progress_page(project: str | Path) -> Path:
    """The page showing the current round live: ``.thearc/cache/progress.html``."""
    return project_root(project) / CACHE / "progress.html"


def write_waiting_page(path: Path, message: str) -> None:
    """A page that says ``message`` and reloads itself, until the round's flow replaces it."""
    from thearc.ui import FONTS, THEME_CSS, THEME_JS

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="refresh" content="2"><title>/evolve</title>\n{FONTS}<style>{THEME_CSS}</style></head>'
        '<body><div class="page"><header class="top"><div><div class="eyebrow">/evolve</div>'
        f'<h1>{message}</h1><div class="meta"><span>this page reloads itself</span></div></div>'
        '<div class="topright"><button id="theme" type="button" class="theme-toggle">◐ light</button></div></header>'
        f'</div><script id="thearc-theme">{THEME_JS}</script></body></html>\n', encoding="utf-8")


def run(project: str | Path = ".", *, recipe: str | Path | Recipe | None = None,
        scales: Sequence[Any] = ()) -> Proposal:
    """Run a round (see the module): returns the proposal, or why there is none. ``recipe`` replaces the installed
    evolve skill's ``evolve.py``: another script, or a ``propose`` function."""
    place = _place(project)
    root, workspace, cache = place.root, place.workspace, place.cache
    with _locked(cache):
        write_waiting_page(progress_page(root), "Preparing the round…")
        _no_proposal(workspace)
        if _pending(workspace):
            raise ValueError(f"the workspace has uncommitted edits: {_pending(workspace)}")
        if _behind_remote(workspace):
            raise ValueError(f"{_main(workspace)} is behind origin; fetch and fast-forward it first")
        drift = MetaAgent.from_workspace(workspace).sync_installation(root, [place.harness])
        release = drift.agent
        base = _git(workspace, "rev-parse", "HEAD").decode().strip()

        state_file = cache / "round.json"
        state = json.loads(state_file.read_text()) if state_file.is_file() else {}
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
        round_id = state["round"] if state.get("base") == base else stamp
        folder = cache / "rounds" / round_id
        folder.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"round": round_id, "base": base}))

        if callable(recipe):
            module = ModuleType("thearc_recipe")
            module.propose = recipe  # type: ignore[attr-defined]
        else:
            module = load_recipe(Path(recipe) if recipe is not None
                                 else recipe_folder(root, place.harness) / "evolve.py")
        with Sessions(cache / "sessions.sqlite") as sessions:
            evolution = Evolution(root, release, round_id, folder, sessions, [place.harness],
                                  _installed_at(workspace, release), progress=progress_page(root))
            evolution.log.info(f"round {round_id} on {release.name} {release.version} ({place.harness})")
            for entry in drift.entries:
                evolution.log.info(f"hand edit committed first: {entry.kind} {entry.target}")
            try:
                result = module.propose(evolution)
            except Exception:
                evolution.log.exception("the round failed")
                raise
            finally:
                evolution.close()
                if evolution.progress.is_file():
                    shutil.copy(evolution.progress, folder / "progress.html")
            found = evolution.found
        drifted = tuple(f"{entry.kind} {entry.target}" for entry in drift.entries)
        if result is None or result.revision == release.revision:
            _finish_round(workspace, folder, state_file)
            return Proposal(None, release.version, evolution.reason or "the recipe proposed no change",
                            drift=drifted, conflicts=drift.conflicts, sessions=found,
                            progress=str(folder / "progress.html"))

        root_of_result = result.workspace
        assert root_of_result is not None
        commit_journal(root_of_result)
        head = _git(root_of_result, "rev-parse", "HEAD").decode().strip()
        version = bump_version(release.version, "patch")
        _git(workspace, "branch", "-f", branch_name(workspace, "proposal", version), head)
        _finish_round(workspace, folder, state_file)
        _git(workspace, "checkout", "-q", branch_name(workspace, "proposal", version))
        report = folder / "report.html"
        _write_report(workspace, report, list(scales) or [getattr(module, "USEFULNESS", None)])
        proposed = MetaAgent.from_workspace(workspace)
        diff = release.diff(proposed)
        (folder / "diff.patch").write_text(diff, encoding="utf-8")
        return Proposal(version, release.version, None, _changes(workspace, base, head), diff, str(report),
                        drifted, drift.conflicts, found, str(folder / "progress.html"))


def _finish_round(workspace: Path, folder: Path, state_file: Path) -> None:
    """Remove the round's worktrees (their branches stay, for the report) and its resume state."""
    for kind in ("candidates", "merged"):
        base = folder / kind
        worktrees = [base] if kind == "merged" else (sorted(base.iterdir()) if base.is_dir() else [])
        for tree in worktrees:
            if (tree / ".git").exists():
                _git(workspace, "worktree", "remove", "--force", str(tree))
    state_file.unlink(missing_ok=True)


def _write_report(workspace: Path, path: Path, scales: list[Any]) -> None:
    from thearc.resources.report import write_report

    write_report(workspace, path, scales=[scale for scale in scales if scale is not None])


def diff(project: str | Path = ".") -> Proposal:
    """The open proposal, edits not yet amended included, against the installed release."""
    workspace = _place(project).workspace
    version = _proposal_version(workspace)
    if version is None:
        raise ValueError("no proposal is open; run `thearc evolve` first")
    main = _main(workspace)
    release = agent_at(workspace, main)
    assert release is not None
    current = MetaAgent.from_workspace(workspace)
    base = _git(workspace, "merge-base", main, "HEAD").decode().strip()
    head = _git(workspace, "rev-parse", "HEAD").decode().strip()
    return Proposal(version, release.version, None, _changes(workspace, base, head), release.diff(current))


def amend(project: str | Path = ".", message: str = "", *, author: str = "user") -> tuple[str, ...]:
    """Commit the edits made in the workspace to the open proposal as one change with ``message`` as its reason,
    checked as a curator's commit is; returns what changed."""
    place = _place(project)
    workspace = place.workspace
    with _locked(place.cache):
        if _proposal_version(workspace) is None:
            raise ValueError("no proposal is open; amend one after `thearc evolve` proposes it")
        before = _git(workspace, "rev-parse", "HEAD").decode().strip()
        tools = CurationTools(workspace, author, email="user@thearc.local", frozen_allowed=True)
        tools.commit_change(message)
        journal = Journal.load(workspace / JOURNAL_FILE)
        entries = record_commits(workspace, journal, since=before, amendment=True)
        if entries:
            Journal.append(workspace / JOURNAL_FILE, entries)
            _git(workspace, "add", JOURNAL_FILE)
            commit(workspace, f"Record amendment: {message.strip().splitlines()[0]}")
        return tools.commits[-1].changed


def apply(project: str | Path = ".", *, push: bool = False) -> Applied:
    """Release the open proposal, move the workspace's ``main`` to it, and install it. With ``push``, ``main`` and the
    release branch are pushed to the workspace's ``origin``. The project then shows the submodule moved, next to the
    installed files: commit them together."""
    place = _place(project)
    workspace = place.workspace
    with _locked(place.cache):
        version = _proposal_version(workspace)
        if version is None:
            raise ValueError("no proposal is open; run `thearc evolve` first")
        if pending := _pending(workspace):
            raise ValueError(f"amend or undo the workspace's edits first: {pending}")
        remote = remote_of(place.root)
        if push and remote is None:
            raise ValueError("the workspace has no remote to push to: run `thearc evolve remote URL` first")
        proposal_branch = _current_branch(workspace)
        MetaAgent.from_workspace(workspace).release(version, include_scales=True)
        main = _main(workspace)
        _git(workspace, "checkout", "-q", main)
        try:
            _git(workspace, "merge", "-q", "--ff-only", proposal_branch)
        except GitError:
            _git(workspace, "checkout", "-q", proposal_branch)
            raise ValueError(f"{main} moved since the proposal was made; discard it and run again") from None
        _git(workspace, "branch", "-D", proposal_branch)
        released = MetaAgent.from_workspace(workspace)
        installed = released.install(place.harness, place.root, force=True).files
        if push:
            _git(workspace, "push", "-q", "origin", "main", branch_name(workspace, "release", version))
        return Applied(released.version or version, (place.harness,), tuple(installed), remote if push else None)


def discard(project: str | Path = ".") -> str:
    """Drop the open proposal and its edits; the workspace goes back to the installed release."""
    place = _place(project)
    workspace = place.workspace
    with _locked(place.cache):
        version = _proposal_version(workspace)
        if version is None:
            raise ValueError("no proposal is open")
        proposal_branch = _current_branch(workspace)
        _git(workspace, "checkout", "-q", "--force", _main(workspace))
        _git(workspace, "clean", "-q", "-fd", "--", ".")
        _git(workspace, "branch", "-D", proposal_branch)
        return version


def ignore(project: str | Path, patterns: Sequence[str], *, remove: bool = False) -> MetaAgent:
    """Ignore ``patterns`` (or stop) in the installed release, and install it so their files are left alone."""
    place = _place(project)
    with _locked(place.cache):
        _no_proposal(place.workspace)
        agent = MetaAgent.from_workspace(place.workspace).ignore_targets(patterns, remove=remove)
        if remove:
            agent = agent.sync_installation(place.root, [place.harness],
                                            message="Adopt what is no longer ignored").agent
        agent.install(place.harness, place.root, force=True)
        return agent


def rollback(project: str | Path, version: str) -> Applied:
    """Install release ``version`` again, as a new version on top of the installed one (history is kept)."""
    place = _place(project)
    workspace = place.workspace
    with _locked(place.cache):
        _no_proposal(workspace)
        old = agent_at(workspace, branch_name(workspace, "release", version))
        if old is None:
            raise ValueError(f"no release {version}")
        current = MetaAgent.from_workspace(workspace)
        restored = current.model_copy(update={key: getattr(old, key) for key in (
            "skills", "context", "resources", "extensions", "hooks", "mcps", "owns_hooks")})
        restored._workspace = workspace
        from thearc.resources.git import commit_configuration

        commit_configuration(restored, f"Roll back to {version}", author="user")
        released = MetaAgent.from_workspace(workspace).release(include_scales=True)
        released.install(place.harness, place.root, force=True)
        return Applied(released.version or "", (place.harness,))


def status(project: str | Path = ".") -> dict[str, Any]:
    """Where the project's evolution stands."""
    place = _place(project)
    agent = MetaAgent.from_workspace(place.workspace)
    main = agent_at(place.workspace, _main(place.workspace))
    recipe = recipe_folder(place.root, place.harness) / "evolve.py"
    runtime = re.search(r'^RUNTIME = "([^"]*)"', recipe.read_text(encoding="utf-8"), re.MULTILINE) if (
        recipe.is_file()) else None
    return {"project": str(place.root), "harness": place.harness, "agent": agent.name,
            "installed": main.version if main else None, "proposal": _proposal_version(place.workspace),
            "runtime": runtime[1] if runtime else None, "recipe": str(recipe), "remote": remote_of(place.root),
            "ignored": agent.ignore,
            "skills": sorted(agent.skills), "context": sorted(agent.context), "hooks": sorted(agent.hooks)}
