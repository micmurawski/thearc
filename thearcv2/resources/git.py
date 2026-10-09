"""Turning git commits of a workspace into journal entries.

An agent or a person edits the workspace files and commits each change with its
reason as the commit message. ``record_commits`` walks the commits the journal
has not seen yet and, for each, compares the workspace with its parent commit:
every changed target gets its own Added / Modified / Moved / Removed entry with
the commit message as ``reason`` and the commit hash as ``commit``. A commit that
touches two sections yields two entries with the same reason; a section that
appears in a commit is Added. Only the main line of history (first parents) is
followed. The repository is only read.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from thearcv2.resources.agent import MetaAgent
from thearcv2.resources.changes import detect_changes
from thearcv2.resources.journal import Entry, Journal, Moved
from thearcv2.resources.targets import ResourceTarget
from thearcv2.resources.workspace import JOURNAL_FILE, MANIFEST, parse_workspace


class GitError(RuntimeError):
    """A git command failed, or a commit does not hold a readable workspace."""


def _git(repo: str | Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=False)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def commit(repo: str | Path, message: str, *, author: str = "thearc", email: str = "thearc@localhost") -> str:
    """Commit what is staged in ``repo`` under ``author`` (never signed); returns the new commit."""
    _git(repo, "-c", f"user.name={author}", "-c", f"user.email={email}", "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").decode().strip()


@dataclass(frozen=True)
class Commit:
    sha: str
    parent: str | None
    at: datetime
    author: str
    message: str


def _prefix(path: str) -> str:
    path = PurePosixPath(path).as_posix().strip("/") if path else ""
    return "" if path in {"", "."} else path + "/"


def workspace_commits(repo: str | Path, *, path: str = "", rev: str = "HEAD") -> list[Commit]:
    """Commits on the first-parent line of ``rev`` that touch the workspace at ``path``, oldest first."""
    parents = {}
    for line in _git(repo, "rev-list", "--first-parent", "--parents", rev).decode().splitlines():
        sha, *rest = line.split()
        parents[sha] = rest[0] if rest else None
    output = _git(
        repo,
        "log",
        "--first-parent",
        "--reverse",
        "--format=%H%x00%cI%x00%an%x00%B%x1e",
        rev,
        "--",
        _prefix(path) or ".",
    ).decode()
    commits = []
    for record in output.split("\x1e"):
        if not record.strip():
            continue
        sha, at, author, message = record.lstrip("\n").split("\x00", 3)
        commits.append(Commit(sha, parents.get(sha), datetime.fromisoformat(at), author, message.strip()))
    return commits


def agent_at(repo: str | Path, commit: str, *, path: str = "") -> MetaAgent | None:
    """The MetaAgent configuration stored in ``commit`` (journal excluded), or ``None`` if it has no workspace."""
    prefix = _prefix(path)
    listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, "--", prefix or ".")
    names = []
    for item in listing.split(b"\0"):
        if not item:
            continue
        meta, name = item.decode().split("\t", 1)
        relative = name[len(prefix) :]
        if not name.startswith(prefix) or relative.split("/", 1)[0].startswith(".") or relative == JOURNAL_FILE:
            continue
        if meta.split()[0] not in {"100644", "100755"}:
            raise GitError(f"{commit}: {name} is not a regular file")
        names.append((name, relative))
    if not any(relative == MANIFEST for _, relative in names):
        return None
    batch = _git(repo, "cat-file", "--batch", stdin="".join(f"{commit}:{name}\n" for name, _ in names).encode())
    texts, position = {}, 0
    for _, relative in names:
        header_end = batch.index(b"\n", position)
        size = int(batch[position:header_end].split()[2])
        texts[relative] = batch[header_end + 1 : header_end + 1 + size].decode("utf-8")
        position = header_end + 1 + size + 1
    try:
        return parse_workspace(texts)
    except (ValueError, ValidationError, KeyError, TypeError) as error:
        raise GitError(f"{commit}: invalid workspace: {error}") from error


def _before(entry: Entry) -> ResourceTarget:
    """The address a change's target had before the commit."""
    return entry.previous if isinstance(entry, Moved) else entry.target


def record_commits(
    repo: str | Path, journal: Journal, *, path: str = "", rev: str = "HEAD", since: str | None = None, **extra: Any
) -> list[Entry]:
    """Append entries for every workspace commit after the last one ``journal`` has recorded; returns them.

    ``since`` marks a commit the journal already reflects although no entry
    names it (e.g. a journal built before the workspace was committed); it and
    earlier commits are skipped. Running it again records nothing new. Each
    entry's ``extra`` holds the commit author plus any keyword arguments.
    """
    commits = workspace_commits(repo, path=path, rev=rev)
    seen = journal.recorded_commits()
    if since is not None:
        seen.add(_git(repo, "rev-parse", "--verify", f"{since}^{{commit}}").decode().strip())
    start = max((index + 1 for index, commit in enumerate(commits) if commit.sha in seen), default=0)
    loaded: dict[str, MetaAgent | None] = {}

    def load(sha: str | None) -> MetaAgent | None:
        if sha is None:
            return None
        if sha not in loaded:
            loaded[sha] = agent_at(repo, sha, path=path)
        return loaded[sha]

    recorded: list[Entry] = []
    for commit in commits[start:]:
        after = load(commit.sha)
        if after is None or commit.sha in seen:
            continue
        ids = {**after.identities, **journal.ids()}  # Lineage IDs, by the addresses before this commit.
        entries = detect_changes(
            load(commit.parent),
            after,
            reason=commit.message,
            at=commit.at,
            commit=commit.sha,
            parent=commit.parent,
            author=commit.author,
            **extra,
        )
        touched = sorted({ids.get(_before(entry), ids.get(entry.target, entry.target.id)) for entry in entries})
        entries = [entry.model_copy(update={"touched": touched}) for entry in entries]
        journal.record(*entries)
        recorded += entries
    return recorded
