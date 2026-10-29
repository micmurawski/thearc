"""Setting a project up for evolution.

``thearc skills install self-evolve --project . --harness claude`` adopts the project's configuration *for Claude* as a
MetaAgent, keeps it in a workspace repository at ``.thearc/workspace`` (a git submodule of the project), and installs
the evolve skill into Claude's skills folder. A project evolves one harness's configuration, from that
harness's sessions. The evolve skill is not part of the MetaAgent (``ignore: ["skill:evolve"]``): it is installed
beside it, recorded as agent ``thearc-evolve``, and never evolved.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import cast

from thearc.harnesses import Harness
from thearc.resources import MetaAgent, Skill
from thearc.resources.git import GitError, _git
from thearc.resources.markdown import split_frontmatter

SKILL = "evolve"
SKILL_PATTERN = f"skill:{SKILL}"
EVOLVE_AGENT = "thearc-evolve"  # The evolve skill's owner in the install record.
HARNESSES: tuple[Harness, ...] = ("claude", "codex", "pi", "opencode")
RUNTIMES: tuple[Harness, ...] = ("claude", "codex", "pi")  # Harnesses that can reflect, curate and merge.
WORKSPACE = Path(".thearc/workspace")
CACHE = Path(".thearc/cache")  # Rounds and the session index: local, never committed.
FIRST_VERSION = "1.0.0"


def thearc_version() -> str:
    try:
        return metadata.version("thearc")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def evolve_skill(harness: Harness, runtime: Harness) -> Skill:
    """The evolve skill for ``harness``: ``SKILL.md``, the default recipe ``evolve.py`` with ``HARNESS`` (whose
    sessions it reads) and ``RUNTIME`` (which reflects and curates) set, and the recipe's prompts in ``prompts/``. Its
    frontmatter names the thearc version it was made with."""
    folder = resources.files("thearc.evolve") / "skill"
    text = (folder / "SKILL.md").read_text(encoding="utf-8").replace("HARNESS", harness)
    frontmatter, body, _ = split_frontmatter(text)
    frontmatter = dict(frontmatter or {})
    script = (folder / "evolve.py").read_text(encoding="utf-8")
    for constant, value in (("HARNESS", harness), ("RUNTIME", runtime)):
        script = re.sub(rf'^{constant} = "[^"]*"', f'{constant} = "{value}"', script, count=1, flags=re.MULTILINE)
    prompts = {f"prompts/{name}.md": (folder / "prompts" / f"{name}.md").read_text(encoding="utf-8")
               for name in ("reflect", "curate")}
    return Skill(name=SKILL, description=frontmatter.pop("description"), instructions=body,
                 metadata={"thearc": thearc_version()}, files={"evolve.py": script, **prompts})


def project_root(path: str | Path) -> Path:
    """The root of the git repository holding ``path``; a project must be one."""
    try:
        return Path(_git(Path(path), "rev-parse", "--show-toplevel").decode().strip()).resolve()
    except GitError:
        raise ValueError(f"{Path(path).resolve()} is not in a git repository; evolution keeps its history there "
                         "(run `git init` first)") from None


def project_harness(project: str | Path) -> Harness:
    """The harness whose configuration ``project`` evolves (where its MetaAgent is installed)."""
    from thearc.resources.harness import installed_agents

    root = project_root(project)
    if not (root / WORKSPACE / ".git").exists():
        raise ValueError("this project is not set up for evolution: run "
                         "`thearc skills install self-evolve --project . --harness <harness>`")
    name = MetaAgent.from_workspace(root / WORKSPACE, journal=False).name
    harnesses = list(installed_agents(root).get(name, {}))
    if len(harnesses) != 1:
        raise ValueError(f"{name} should be installed for one harness, not {harnesses or 'none'}; reinstall it")
    return cast("Harness", harnesses[0])


def recipe_folder(project: str | Path, harness: Harness) -> Path:
    """The installed evolve skill of ``harness``: its ``evolve.py`` and ``prompts/``."""
    from thearc.resources.harness import get_harness

    return project_root(project) / get_harness(harness).location("skills") / SKILL


def adopt(project: str | Path, harness: Harness, *, name: str | None = None, ignore: Sequence[str] = ()) -> MetaAgent:
    """The project's configuration for ``harness`` as a MetaAgent at its first version, without the evolve skill
    (``ignore`` holds ``skill:evolve``) and without what ``ignore`` names."""
    root = Path(project).resolve()
    patterns = list(dict.fromkeys([SKILL_PATTERN, *ignore]))
    imported = MetaAgent.from_installation(harness, root, name=name or root.name, ignore=patterns)
    return imported.model_copy(update={"version": FIRST_VERSION})


@dataclass(frozen=True)
class Setup:
    agent: MetaAgent  # The project's MetaAgent, on its workspace.
    harness: Harness
    runtime: Harness


def install_evolve_skill(project: str | Path, harness: Harness, *, runtime: Harness | None = None,
                         ignore: Sequence[str] = (), remote: str | None = None) -> Setup:
    """Set ``project`` up for ``/evolve`` in ``harness``: adopt its configuration for that harness into a workspace
    repository at ``.thearc/workspace``, added to the project as a git submodule (once), and install the evolve skill
    into the harness's skills folder. ``runtime`` reflects and curates (default: the harness itself). With ``remote``,
    the workspace's ``origin`` is set to it and ``main`` is pushed there; the submodule records that URL, so a
    teammate's clone fetches the workspace from it. Run again, it reinstalls the evolve skill, e.g. with another
    runtime; a project evolves one harness."""
    from thearc.resources.harness import InstallConflict, install, installed_agents

    if harness not in HARNESSES:
        raise ValueError(f"cannot evolve {harness!r}; harnesses: {', '.join(HARNESSES)}")
    runtime = runtime or harness
    if runtime not in RUNTIMES:
        raise ValueError(f"{runtime} cannot reflect or curate; pass a runtime ({', '.join(RUNTIMES)})")
    root = project_root(project)
    skill = recipe_folder(root, harness) / "SKILL.md"
    if skill.is_file() and harness not in installed_agents(root).get(EVOLVE_AGENT, {}):
        raise ValueError(f"{skill.relative_to(root)} is a skill of the project's own named {SKILL!r}; rename it "
                         "to install the evolve skill")
    folder = root / WORKSPACE
    if (folder / ".git").exists():
        if (evolved := project_harness(root)) != harness:
            raise ValueError(f"this project already evolves its {evolved} configuration; a project evolves one "
                             "harness")
        agent = MetaAgent.from_workspace(folder)
        if remote is not None:
            set_remote(root, remote)
    else:
        _unignore_in_git(root, f"/{WORKSPACE.as_posix()}/")  # An earlier thearc kept it out of the project.
        agent = adopt(root, harness, ignore=ignore).init_workspace(
            folder, reason=f"Adopt {root.name}'s {harness} configuration")
        if remote is not None:
            _git(folder, "remote", "add", "origin", remote)
            _git(folder, "push", "-q", "-u", "origin", "main")
        # An existing repository at the path is added as it is; without a remote the URL is a placeholder.
        _git(root, "submodule", "add", "-q", remote or f"./{WORKSPACE.as_posix()}", WORKSPACE.as_posix())
        agent.install(harness, root, force=True)
    tool = MetaAgent(name=EVOLVE_AGENT, version=thearc_version(), skills={SKILL: evolve_skill(harness, runtime)})
    try:
        install(tool, harness, root)
    except InstallConflict as error:
        raise ValueError(str(error)) from None
    _ignore_in_git(root, f"/{CACHE.as_posix()}/")  # Rounds and the session index stay local.
    return Setup(agent, harness, runtime)


def set_remote(project: str | Path, url: str) -> None:
    """Point the workspace's ``origin`` at ``url``, and the project's submodule too (``.gitmodules``)."""
    root = project_root(project)
    folder = root / WORKSPACE
    remotes = _git(folder, "remote").decode().split()
    _git(folder, "remote", "set-url" if "origin" in remotes else "add", "origin", url)
    _git(root, "submodule", "set-url", WORKSPACE.as_posix(), url)


def remote_of(project: str | Path) -> str | None:
    """The workspace's ``origin``, if it has one."""
    try:
        return _git(project_root(project) / WORKSPACE, "remote", "get-url", "origin").decode().strip() or None
    except GitError:
        return None


def uninstall_evolve_skill(project: str | Path) -> Harness:
    """Remove the evolve skill; the evolved configuration and its workspace stay. Returns the harness."""
    from thearc.resources.harness import uninstall

    harness = project_harness(project)
    uninstall(harness, project_root(project), name=EVOLVE_AGENT)
    return harness


def init_from_remote(project: str | Path) -> MetaAgent:
    """Check out the project's workspace submodule on ``main``: for a teammate who cloned a project another set up for
    evolution without ``--recurse-submodules``."""
    root = project_root(project)
    folder = root / WORKSPACE
    _git(root, "submodule", "update", "--init", "--", WORKSPACE.as_posix())
    try:
        _git(folder, "checkout", "-q", "main")  # A submodule is checked out detached; rounds work on main.
    except GitError:
        _git(folder, "checkout", "-q", "-B", "main", "origin/main")
    return MetaAgent.from_workspace(folder)


def _ignore_in_git(root: Path, line: str) -> None:
    gitignore = root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    if line not in text.splitlines():
        gitignore.write_text(text + ("" if not text or text.endswith("\n") else "\n") + line + "\n", encoding="utf-8")


def _unignore_in_git(root: Path, line: str) -> None:
    gitignore = root / ".gitignore"
    if gitignore.is_file() and line in (lines := gitignore.read_text(encoding="utf-8").splitlines()):
        gitignore.write_text("".join(f"{kept}\n" for kept in lines if kept != line), encoding="utf-8")
