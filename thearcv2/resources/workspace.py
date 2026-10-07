"""Workspace: the canonical, editable, git-trackable directory of a MetaAgent.

The configuration uses the agent-files layout (see ``thearcv2.resources.files``), so
file references, workspace paths, and git diffs agree. Next to it:

    agent.json                  {"schema_version", "name", "version"}
    journal.jsonl               one journal entry per line, in ID (= recording) order
    .gitattributes              ``journal.jsonl merge=union``: branches that each appended
                                entries merge without conflicts

Top-level entries starting with ``.`` (such as ``.git``) are ignored; any other
unknown file is an error. Nothing is installed or executed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from thearcv2.resources.agent import MCP, SKILL_FILE, AgentResource, ContextDocument, Hook, MetaAgent
from thearcv2.resources.files import HOOKS_FILE, MCP_FILE
from thearcv2.resources.journal import Entry, Journal
from thearcv2.resources.markdown import split_frontmatter

MANIFEST = "agent.json"
JOURNAL_FILE = "journal.jsonl"
GITATTRIBUTES = ".gitattributes"
UNION_MERGE = f"{JOURNAL_FILE} merge=union"
SCHEMA_VERSION = 2
CONFIG_DIRS = ("skills", "context", "rules", "workflows", "commands")
_ENTRY: TypeAdapter[Entry] = TypeAdapter(Entry)


def _root(path: str | Path) -> Path:
    root = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("workspace paths must not contain symlinks")
    return root


def dump_journal(entries: list[Entry]) -> str:
    """One compact JSON entry per line, sorted by ID."""
    return "".join(entry.model_dump_json() + "\n" for entry in sorted(entries, key=lambda entry: entry.id))


def load_journal(text: str) -> list[Entry]:
    """Parse ``journal.jsonl``. Lines may be out of order or repeated (as after a union merge);
    the same ID with different content is an error, since entries are never rewritten."""
    entries: dict[str, Entry] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = _ENTRY.validate_json(line)
        except ValidationError as error:
            raise ValueError(f"{JOURNAL_FILE}:{number}: invalid entry: {error}") from None
        if entry.id in entries and entries[entry.id] != entry:
            raise ValueError(f"{JOURNAL_FILE}:{number}: entry {entry.id} differs from an earlier line")
        entries[entry.id] = entry
    return sorted(entries.values(), key=lambda entry: entry.id)


def workspace_files(agent: MetaAgent, *, journal: bool = True) -> dict[str, str]:
    """Every workspace file as text; ``journal=False`` gives a reflection-safe view."""
    manifest = {"schema_version": SCHEMA_VERSION, "name": agent.name, "version": agent.version}
    files = {MANIFEST: json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", **agent.files().files}
    if journal and agent.journal.entries:
        files[JOURNAL_FILE] = dump_journal(agent.journal.entries)
    return files


def _is_managed(relative: str) -> bool:
    top = relative.split("/", 1)[0]
    return relative in {MANIFEST, HOOKS_FILE, MCP_FILE} or top in CONFIG_DIRS


def write_workspace(agent: MetaAgent, path: str | Path, *, journal: bool = True, overwrite: bool = False) -> Path:
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
            if relative.startswith(".") or not _is_managed(relative):
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
        path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + UNION_MERGE + "\n",
                        encoding="utf-8")


def read_workspace(path: str | Path, *, max_bytes: int = 10_000_000, max_files: int = 10_000) -> MetaAgent:
    """Read a workspace written by ``write_workspace`` or edited by hand."""
    if max_bytes < 1 or max_files < 1:
        raise ValueError("workspace limits must be positive")
    root = _root(path)
    if not root.is_dir():
        raise ValueError(f"workspace must be an existing directory: {root}")
    texts: dict[str, str] = {}
    remaining = max_bytes
    for item in sorted(root.iterdir()):
        if item.name.startswith("."):
            continue
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
    try:
        return parse_workspace(texts)
    except (ValidationError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"invalid workspace: {error}") from error


def _object(text: str, path: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")  # noqa: TRY004 -- malformed input
    return data


def parse_workspace(texts: dict[str, str]) -> MetaAgent:
    """Build a MetaAgent from workspace files given as ``{relative path: text}`` (dot-files excluded)."""
    texts = dict(texts)
    if MANIFEST not in texts:
        raise ValueError(f"missing {MANIFEST}")
    manifest = _object(texts.pop(MANIFEST), MANIFEST)
    if set(manifest) != {"schema_version", "name", "version"} or manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported {MANIFEST}")
    skills: dict[str, dict[str, Any]] = {}
    context, resources, entries = [], [], []
    hooks = [Hook(name=name, **fields) for name, fields in _object(texts.pop(HOOKS_FILE, "{}"), HOOKS_FILE).items()]
    mcps = [MCP(name=name, **fields) for name, fields in _object(texts.pop(MCP_FILE, "{}"), MCP_FILE).items()]
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
                skill.update(description=metadata.pop("description", ""), metadata=metadata, instructions=body,
                             has_instructions=True)
            else:
                skill["files"][file] = text
        elif top == "context" and rest:
            context.append(ContextDocument(filename=rest, content=text))
        elif top in {"rules", "workflows", "commands"} and rest:
            resources.append(AgentResource.model_validate({"location": top, "path": rest, "content": text}))
        elif relative == JOURNAL_FILE:
            entries = load_journal(text)
        else:
            raise ValueError(f"unexpected workspace file: {relative}")
    for name, skill in skills.items():
        if not skill.pop("has_instructions", False):
            raise ValueError(f"skills/{name} has no {SKILL_FILE}")
    return MetaAgent.model_validate({
        "name": manifest["name"], "version": manifest["version"], "skills": list(skills.values()), "hooks": hooks,
        "mcps": mcps, "context": context, "resources": resources, "journal": Journal(entries=entries)})
