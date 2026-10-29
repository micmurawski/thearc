"""Where a MetaAgent to install comes from: its workspace repository, local or remote, at a release.

``agent_from(source, version)`` reads the configuration committed at ``release/<version>`` — the release branch
``MetaAgent.release`` makes, with the workspace's branch prefix if it has one — or at ``main`` without a version.
Nothing is checked out. A URL (``https://…``, ``ssh://…``, ``git@host:…``, ``file://…``) is cloned into a temporary
bare repository first; a local path may be a workspace, a clone of one, or a project's ``.thearc/workspace``.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from thearc.resources.agent import MetaAgent
from thearc.resources.git import GitError, _git, agent_at, branch_prefix

_URL = re.compile(r"^(https?://|ssh://|git://|file://|[\w.-]+@[\w.-]+:)")


def is_url(source: str | Path) -> bool:
    """Whether ``source`` names a remote repository rather than a local folder."""
    return bool(_URL.match(str(source)))


def agent_from(source: str | Path, version: str | None = None) -> MetaAgent:
    """The MetaAgent released as ``version`` in the workspace repository at ``source`` (its ``main`` when ``version``
    is None); ``ValueError`` when there is no such release or no workspace there."""
    if is_url(source):
        with tempfile.TemporaryDirectory(prefix="thearc-agent-") as folder:
            clone = Path(folder) / "workspace.git"
            try:
                _git(folder, "clone", "--quiet", "--bare", str(source), str(clone))
            except GitError as error:
                raise ValueError(f"cannot clone {source}: {error}") from None
            return _at(clone, version, str(source))
    path = Path(source).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{path} is not a folder or a repository URL")
    try:
        _git(path, "rev-parse", "--git-dir")
    except GitError:
        if version is not None:
            raise ValueError(f"{path} is not a git repository, so it has no releases") from None
        return MetaAgent.from_workspace(path, journal=False)  # A plain workspace folder, as it is.
    return _at(path, version, str(path))


def releases(repo: str | Path) -> list[str]:
    """The versions released in the workspace repository at ``repo``, from its (or its remote's) release branches."""
    names = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes").decode().split()
    found = {name.rsplit("release/", 1)[1] for name in names if "release/" in name}
    return sorted(found, key=_version_key)


def _at(repo: Path, version: str | None, where: str) -> MetaAgent:
    prefix = branch_prefix(repo)
    if version is None:
        names = [f"{prefix}main", f"origin/{prefix}main", "HEAD"]
    else:
        names = [f"{prefix}release/{version}", f"origin/{prefix}release/{version}"]
        listed = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes").decode()
        names += [name for name in listed.split() if name.endswith(f"/release/{version}")]  # Any other prefix.
    for name in dict.fromkeys(names):
        try:
            commit = _git(repo, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}").decode().strip()
        except GitError:
            continue
        agent = agent_at(repo, commit)
        if agent is not None:
            return agent
    if version is not None:
        known = releases(repo)
        raise ValueError(f"{where} has no release {version}; "
                         + (f"releases: {', '.join(known)}" if known else "it has no releases yet"))
    raise ValueError(f"{where} holds no MetaAgent workspace on main")


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"[.\-+]", version))
