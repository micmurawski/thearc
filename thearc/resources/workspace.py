"""Workspace: the canonical, editable, git-trackable directory of a MetaAgent.

The configuration uses the agent-files layout (see ``thearc.resources.files``), so
file references, workspace paths, and git diffs agree. Next to it:

    agent.json                  {"schema_version", "name", "version"}
    hooks.jsonc, mcp.jsonc,     the agent files' ``hooks.json`` / ``mcp.json`` / ``owned_hooks.json``,
    owned_hooks.jsonc           as JSONC so their entries can carry markers
    journal.jsonl               one journal entry per line, in ID (= recording) order
    .gitattributes              ``journal.jsonl merge=union``: branches that each appended
                                entries merge without conflicts

Top-level entries starting with ``.`` (such as ``.git``) are ignored, and so are other top-level
files the workspace does not manage (a repository's ``README.md`` or ``LICENSE``); an unknown file
inside a configuration folder is an error. Resources are never executed. ``init_workspace`` also
initializes git and commits the initial configuration and journal.

Configuration files carry **markers** (see ``thearc.resources.markers``): each
target's lineage ID. Reading a workspace strips them, so the MetaAgent — and its
revision, its installations, and the files reflectors read — never contains them.
Ratings are not in the files; ``agent_outline`` renders them (``RATINGS.md`` on release).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from thearc.resources import markers
from thearc.resources.agent import (
    MCP,
    SKILL_FILE,
    AgentResource,
    ContextDocument,
    Extension,
    Hook,
    MetaAgent,
)
from thearc.resources.files import HOOKS_FILE, MCP_FILE, OWNED_HOOKS_FILE
from thearc.resources.journal import Journal, dump_journal, load_journal
from thearc.resources.markdown import split_frontmatter
from thearc.resources.targets import ResourceTarget

MANIFEST = "agent.json"
JOURNAL_FILE = "journal.jsonl"
GITATTRIBUTES = ".gitattributes"
UNION_MERGE = f"{JOURNAL_FILE} merge=union"
SCHEMA_VERSION = 2
CONFIG_DIRS = ("skills", "context", "rules", "workflows", "commands", "extensions")
# Agent files whose workspace copy is JSONC, so entries can carry markers.
JSONC = {HOOKS_FILE: "hooks.jsonc", MCP_FILE: "mcp.jsonc", OWNED_HOOKS_FILE: "owned_hooks.jsonc"}
_AGENT_NAME = {workspace: agent for agent, workspace in JSONC.items()}


def _root(path: str | Path) -> Path:
    root = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("workspace paths must not contain symlinks")
    return root


def lineage_ids(agent: MetaAgent) -> dict[ResourceTarget, str]:
    """Each target's lineage ID: the journal's, else its marker's, else a new one from its address."""
    ids = {**agent.identities, **agent.journal.ids()}
    return {target: ids.get(target, target.id) for target in agent.files().targets}


def mark_new_targets(path: str | Path) -> dict[ResourceTarget, str]:
    """Write a marker for every target in the workspace at ``path`` that has none yet — a section a curator or
    merger just wrote — holding the lineage ID the journal will record for it; returns them. Without a marker,
    a section's ID depends on its address, so a later rename would start a new history instead of a move."""
    root = _root(path)
    agent = read_workspace(root)
    ids = lineage_ids(agent)
    taken = set(agent.identities.values())
    new = {target: ids[target] for target in ids if target not in agent.identities and ids[target] not in taken}
    if not new:
        return {}
    marked: dict[ResourceTarget, str] = {}
    for relative, text in markers.annotate(agent.files(), {**agent.identities, **new}).items():
        file = root / JSONC.get(relative, relative)
        current = file.read_text(encoding="utf-8")
        added = set(markers.strip(relative, text)[1].values()) - set(markers.strip(relative, current)[1].values())
        if added:
            file.write_text(text, encoding="utf-8")
            marked.update({target: identifier for target, identifier in new.items() if identifier in added})
    return marked


def workspace_files(agent: MetaAgent, *, journal: bool = True) -> dict[str, str]:
    """Every workspace file as text, with a marker holding each target's lineage ID."""
    manifest: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "name": agent.name, "version": agent.version}
    annotated = markers.annotate(agent.files(), lineage_ids(agent))
    files = {
        MANIFEST: json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        **{JSONC.get(path, path): text for path, text in annotated.items()},
    }
    if journal and agent.journal.entries:
        files[JOURNAL_FILE] = dump_journal(agent.journal.entries)
    return files


def init_workspace(
    agent: MetaAgent, path: str | Path, *, branch: str = "main", reason: str = "Initialize MetaAgent workspace"
) -> MetaAgent:
    """Initialize or resume a workspace without mutating the supplied agent.

    The agent must have a version, so the workspace's revision is ``version@rev_…``.
    An existing workspace must have the same name, version and configuration.
    Its journal is retained, and an already committed repository is left alone,
    including pending changes. A plain workspace can be adopted into git.
    """
    from thearc.resources.changes import detect_changes
    from thearc.resources.git import GitError, _git, commit

    if agent.version is None:
        raise ValueError(f"A workspace needs a versioned MetaAgent; give {agent.name!r} a version first")
    root = _root(path)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        saved = read_workspace(root)
        identity = ("name", "version", "owns_hooks", "revision")
        if any(getattr(saved, field) != getattr(agent, field) for field in identity):
            raise ValueError(f"Workspace identity or configuration differs from the MetaAgent: {root}")
    else:
        saved = agent.model_copy(deep=True)
        saved._workspace = root
        if not saved.journal.entries:
            saved.journal.record(*detect_changes(None, saved, reason=reason))
        write_workspace(saved, root, overwrite=root.exists())

    if (root / ".git").exists():  # A git worktree has a .git file instead of a directory.
        try:
            _git(root, "rev-parse", "--verify", "HEAD")
        except GitError:
            pass  # Finish initialization of a repository with no commits yet.
        else:
            return saved
    else:
        _git(root, "init", "-q", "-b", branch)
    paths = list(workspace_files(saved))
    if (root / GITATTRIBUTES).is_file():
        paths.append(GITATTRIBUTES)
    _git(root, "add", "--", *paths)
    commit(root, reason)
    return saved


def is_managed(relative: str) -> bool:
    """Whether a workspace path belongs to the agent: its manifest, structured files, or configuration folders."""
    top = relative.split("/", 1)[0]
    return relative in {MANIFEST, *_AGENT_NAME, *JSONC} or top in CONFIG_DIRS  # Plain JSON names: read to refuse.


def write_workspace(
    agent: MetaAgent,
    path: str | Path,
    *,
    journal: bool = True,
    overwrite: bool = False,
) -> Path:
    """Write the workspace.

    Without ``overwrite`` the directory must not exist. With it, configuration
    files are synchronised (stale ones removed) while the journal stays
    append-only: entries already in ``journal.jsonl`` are kept, and an entry
    whose ID is there with different content is an error. ``journal=False``
    neither writes nor touches the journal.
    """
    root = _root(path)
    files = workspace_files(agent, journal=journal)
    if root.exists() and not overwrite:
        raise FileExistsError(f"workspace already exists: {root}")
    journal_path = root / JOURNAL_FILE
    if journal and journal_path.is_file():
        recorded = {entry.id: entry for entry in load_journal(journal_path.read_text(encoding="utf-8"))}
        for entry in agent.journal.entries:
            if entry.id in recorded and recorded[entry.id] != entry:
                raise ValueError(f"journal entry {entry.id} differs from the recorded one")
        files[JOURNAL_FILE] = dump_journal([*recorded.values(), *agent.journal.entries])
    if overwrite and root.exists():
        for item in sorted(root.rglob("*"), reverse=True):
            relative = item.relative_to(root).as_posix()
            if relative.startswith(".") or not is_managed(relative):
                continue
            if item.is_symlink() or item.is_file():
                if relative not in files:
                    item.unlink()
            elif item.is_dir() and not any(item.iterdir()):
                item.rmdir()
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content)
    if JOURNAL_FILE in files:
        _ensure_union_merge(root / GITATTRIBUTES)
    return root


def _ensure_union_merge(path: Path) -> None:
    """Add ``journal.jsonl merge=union`` to ``.gitattributes``, keeping whatever else it holds."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if UNION_MERGE not in (line.strip() for line in text.splitlines()):
        path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + UNION_MERGE + "\n", encoding="utf-8")


def read_workspace(
    path: str | Path, *, journal: bool = True, max_bytes: int = 10_000_000, max_files: int = 10_000
) -> MetaAgent:
    """Read a workspace written by ``write_workspace`` or edited by hand; the agent remembers it as its workspace,
    and the lineage IDs its markers gave (``agent.identities``).

    The limits apply to the configuration. The journal is read separately and may be any size;
    ``journal=False`` leaves it unread (the agent's journal is then empty, and ``add_reflection``
    only appends to the file).
    """
    agent, identities = _read(path, journal=journal, max_bytes=max_bytes, max_files=max_files)
    agent._workspace = _root(path)
    agent._identities = identities
    return agent


def _read(
    path: str | Path, *, journal: bool = True, max_bytes: int = 10_000_000, max_files: int = 10_000
) -> tuple[MetaAgent, dict[ResourceTarget, str]]:
    """The agent in a workspace and the lineage IDs its markers give."""
    if max_bytes < 1 or max_files < 1:
        raise ValueError("workspace limits must be positive")
    root = _root(path)
    if not root.is_dir():
        raise ValueError(f"workspace must be an existing directory: {root}")
    texts: dict[str, str] = {}
    remaining = max_bytes
    for item in sorted(root.iterdir()):
        if item.name.startswith(".") or item.name == JOURNAL_FILE or not is_managed(item.name):
            continue  # The repository's own files (README, LICENSE, …) are not the agent's.
        for node in [item, *sorted(item.rglob("*"))] if item.is_dir() else [item]:
            if node.is_symlink() or not (node.is_file() or node.is_dir()):
                raise ValueError(f"workspace contains a symlink or special file: {node}")
            if node.is_dir():
                continue
            if len(texts) >= max_files:
                raise ValueError("workspace exceeds the file limit")
            with node.open("rb") as stream:
                content = stream.read(remaining + 1)
            remaining -= len(content)
            if remaining < 0:
                raise ValueError("workspace exceeds the byte limit")
            texts[node.relative_to(root).as_posix()] = content.decode("utf-8")
    if journal and (root / JOURNAL_FILE).is_file():
        if (root / JOURNAL_FILE).is_symlink():
            raise ValueError(f"workspace contains a symlink or special file: {root / JOURNAL_FILE}")
        recorded = Journal.load(root / JOURNAL_FILE)
    else:
        recorded = Journal()
    try:
        agent, identities = parse_workspace(texts)
    except (ValidationError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"invalid workspace: {error}") from error
    agent.journal = recorded
    return agent, identities


def _object(text: str, path: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")  # noqa: TRY004 -- malformed input
    return data


def parse_workspace(texts: dict[str, str]) -> tuple[MetaAgent, dict[ResourceTarget, str]]:
    """Build a MetaAgent from workspace files given as ``{relative path: text}`` (dot-files excluded), with the
    lineage IDs its markers give."""
    texts = dict(texts)
    if MANIFEST not in texts:
        raise ValueError(f"missing {MANIFEST}")
    manifest = _object(texts.pop(MANIFEST), MANIFEST)
    if set(manifest) != {"schema_version", "name", "version"} or manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported {MANIFEST}")
    found: dict[str, dict[int, str]] = {}
    for plain, workspace_name in JSONC.items():
        if plain in texts:
            raise ValueError(f"{plain}: a workspace keeps it as {workspace_name}")
    for workspace_name in [name for name in texts if name in _AGENT_NAME]:
        texts[_AGENT_NAME[workspace_name]] = texts.pop(workspace_name)
    for relative in list(texts):
        if relative != JOURNAL_FILE:
            texts[relative], found[relative] = markers.strip(relative, texts[relative])
    skills: dict[str, dict[str, Any]] = {}
    context, resources, extensions, entries = [], [], [], []
    hooks = [Hook(name=name, **fields) for name, fields in _object(texts.pop(HOOKS_FILE, "{}"), HOOKS_FILE).items()]
    mcps = [MCP(name=name, **fields) for name, fields in _object(texts.pop(MCP_FILE, "{}"), MCP_FILE).items()]
    owned = json.loads(texts.pop(OWNED_HOOKS_FILE, "[]"))
    for relative, text in sorted(texts.items()):
        top, _, rest = relative.partition("/")
        if top == "skills" and "/" in rest:
            name, _, file = rest.partition("/")
            skill = skills.setdefault(name, {"name": name, "files": {}})
            if file == SKILL_FILE:
                frontmatter, body, _ = split_frontmatter(text)
                metadata = dict(frontmatter or {})
                if metadata.pop("name", name) != name:
                    raise ValueError(f"{relative}: frontmatter name must match the skill directory")
                skill.update(
                    description=metadata.pop("description", ""),
                    metadata=metadata,
                    instructions=body,
                    has_instructions=True,
                )
            else:
                skill["files"][file] = text
        elif top == "context" and rest:
            context.append(ContextDocument(filename=rest, content=text))
        elif top in {"rules", "workflows", "commands"} and rest:
            resources.append(AgentResource.model_validate({"location": top, "path": rest, "content": text}))
        elif top == "extensions" and "/" in rest:
            harness, _, path = rest.partition("/")
            extensions.append(Extension.model_validate({"harness": harness, "path": path, "content": text}))
        elif relative == JOURNAL_FILE:
            entries = load_journal(text)
        elif "/" in relative and top in CONFIG_DIRS:
            raise ValueError(f"unexpected workspace file: {relative}")
        # Anything else (a README, a LICENSE, a docs folder) belongs to the repository, not the agent.
    for name, skill in skills.items():
        if not skill.pop("has_instructions", False):
            raise ValueError(f"skills/{name} has no {SKILL_FILE}")
    agent = MetaAgent.model_validate(
        {
            "name": manifest["name"],
            "version": manifest["version"],
            "owns_hooks": owned,
            "skills": list(skills.values()),
            "hooks": hooks,
            "mcps": mcps,
            "context": context,
            "resources": resources,
            "extensions": extensions,
            "journal": Journal(entries=entries),
        }
    )
    return agent, markers.identities(agent.files(), found)

