"""Installation: deploying a MetaAgent into a harness's native project layout, and importing it back.

An installation owns what it wrote. The project's install record (``.thearc/installed.json``)
lists, per installed MetaAgent, the context sections it owns — context documents are shared by
the harnesses that read them — and, per harness, its version, revision, files, hooks, and MCP
servers. Reinstalling replaces exactly those and leaves everything else in the project alone;
installing another agent over something takes its ownership over. Nothing is executed.

- Skills, rules, workflows, commands, and hook scripts are rebuilt as whole
  files. Overwriting a file thearc did not write is a conflict unless forced.
- Context documents (``AGENTS.md``, ``CLAUDE.md``, ...) are written whole when
  thearc creates them. If one already exists, only its top-level sections are
  merged by heading title: a section with the same title is replaced in place,
  a new one is appended, and the rest of the document is kept.
- Hooks and MCP servers are merged into the harness's own configuration files.

Installed files carry the agent's name and version as stamps (``thearc.resources.markers``),
so a session transcript shows which version the harness read; importing removes them.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from thearc.harnesses import Harness
from thearc.resources.agent import MCP, SKILL_FILE, AgentResource, ContextDocument, Extension, Hook, MetaAgent, Skill
from thearc.resources.files import skill_markdown, structured_entry
from thearc.resources.markdown import (
    render_frontmatter,
    replace_sections,
    section_text,
    split_frontmatter,
    top_sections,
)
from thearc.resources.markers import (
    label,
    stamp_file,
    stamp_markdown,
    unstamp_file,
    unstamp_markdown,
)

RECORD = ".thearc/installed.json"
RECORD_SCHEMA_VERSION = 1
CONTEXT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "PI.md")
HookKey = tuple[str, str | None, str]  # (event, matcher, command)


class InstallConflict(FileExistsError):
    """Installing would overwrite something this MetaAgent does not own."""


@dataclass(frozen=True)
class HarnessLayout:
    """A harness's project layout and the native formats of its configuration files.

    All paths are relative to the project root. ``hooks_format``:

    - ``"nested"`` — ``{"hooks": {"PreToolUse": [{"matcher": ..., "hooks": [...]}]}}``,
      merged into ``hooks_file`` next to any other keys it holds (Claude, Codex);
    - ``"flat"`` — the workspace's ``{name: entry}`` format.

    ``mcp_format``: ``"json"`` — ``{"mcpServers": {...}}`` with ``mcp_type_key`` as the
    transport key; ``"toml"`` — Codex ``[mcp_servers.<name>]`` tables. A ``None`` file
    means the harness does not support that resource; it is skipped and reported.
    """

    name: Harness
    base: str
    hooks_file: str | None
    mcp_file: str | None
    hooks_format: Literal["nested", "flat"] = "flat"
    mcp_format: Literal["json", "toml"] = "json"
    mcp_type_key: str = "transport"
    locations: dict[str, str] = field(default_factory=dict)

    def location(self, kind: str) -> str:
        """Directory of ``skills``, ``rules``, ``workflows``, or ``commands``."""
        return f"{self.base}/{self.locations.get(kind, kind)}"


HARNESSES: dict[str, HarnessLayout] = {
    "claude": HarnessLayout(
        "claude",
        ".claude",
        hooks_file=".claude/settings.json",
        mcp_file=".mcp.json",
        hooks_format="nested",
        mcp_type_key="type",
    ),
    "codex": HarnessLayout(
        "codex",
        ".codex",
        hooks_file=".codex/hooks.json",
        mcp_file=".codex/config.toml",
        hooks_format="nested",
        mcp_format="toml",
        locations={"commands": "prompts"},
    ),
    "antigravity": HarnessLayout(
        "antigravity", ".agents", hooks_file=".agents/hooks.json", mcp_file=".agents/mcp_config.json"
    ),
    "pi": HarnessLayout("pi", ".pi", hooks_file=None, mcp_file=None, locations={"commands": "prompts"}),
    # OpenCode reads .opencode/skills (and .agents/.claude skills); hooks and MCP live in plugins and opencode.json.
    "opencode": HarnessLayout(
        "opencode", ".opencode", hooks_file=None, mcp_file=None, locations={"commands": "command"}
    ),
}


def get_harness(harness: Harness | HarnessLayout) -> HarnessLayout:
    if isinstance(harness, HarnessLayout):
        return harness
    try:
        return HARNESSES[harness]
    except KeyError:
        raise ValueError(f"cannot install into harness {harness!r}; supported: {sorted(HARNESSES)}") from None


# --- hooks --------------------------------------------------------------------------------------------------------


def _pascal(event: str) -> str:
    return "".join(part.capitalize() for part in event.split("_"))


def _snake(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def _hook_command(harness: HarnessLayout, hook: Hook) -> str | None:
    if hook.command:
        return hook.command
    if hook.script:
        return f"./{harness.base}/hooks/{hook.name}.sh"
    return None


class _Ownership:
    """Which hooks in a harness file are the MetaAgent's: recorded by a previous install, or matching ``owns_hooks``."""

    def __init__(self, owned: set[HookKey], patterns: list[str]):
        self.owned, self.patterns = owned, patterns

    def __call__(self, key: HookKey) -> bool:
        command = key[2] or ""
        return key in self.owned or any(fnmatch.fnmatchcase(command, pattern) for pattern in self.patterns)


def _flat_entry(hook: Hook) -> dict[str, Any]:
    """A hook in a flat hooks file; its harness scope stays in the marker, not in the harness's file."""
    return {key: value for key, value in structured_entry(hook).items() if key != "harnesses"}


def _flat_key(entry: Any) -> HookKey:
    entry = entry if isinstance(entry, dict) else {}
    return (entry.get("event", "pre_tool_use"), entry.get("matcher"), entry.get("command"))


def _merge_nested_hooks(
    data: dict[str, Any], hooks: dict[str, Hook], harness: HarnessLayout, ours: _Ownership
) -> tuple[dict[str, Any], dict[str, list]]:
    native: dict[str, list] = {}
    for event, groups in data.get("hooks", {}).items():
        kept_groups = []
        for group in groups:
            kept = [
                definition
                for definition in group.get("hooks", [])
                if not ours((_snake(event), group.get("matcher"), definition.get("command")))
            ]
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups:
            native[event] = kept_groups
    keys: dict[str, list] = {}
    for name, hook in sorted(hooks.items()):
        command = _hook_command(harness, hook)
        if command is None:
            continue
        groups = native.setdefault(_pascal(hook.event), [])
        group = next((g for g in groups if g.get("matcher") == hook.matcher), None)
        if group is None:
            group = {"matcher": hook.matcher, "hooks": []} if hook.matcher is not None else {"hooks": []}
            groups.append(group)
        definition = {"type": "command", "command": command, **hook.extra}
        if definition not in group["hooks"]:  # An identical hook already installed by someone else stays theirs.
            group["hooks"].append(definition)
            keys[name] = [hook.event, hook.matcher, command]
    rest = {key: value for key, value in data.items() if key != "hooks"}
    return ({**rest, "hooks": native} if native else rest), keys


def _read_nested_hooks(data: dict[str, Any], names: dict[tuple, str], harness: Harness,
                       scopes: dict[str, list[Harness]]) -> list[Hook]:
    """Hooks in a nested hooks file. Ones a thearc install recorded keep their name and scope; others are
    named by position and limited to ``harness``, whose tool names their matchers use."""
    hooks = []
    for event, groups in data.get("hooks", {}).items():
        for group_index, group in enumerate(groups):
            for hook_index, definition in enumerate(group.get("hooks", [])):
                command = definition.get("command")
                if not command or definition.get("type", "command") != "command":
                    continue
                key = (_snake(event), group.get("matcher"), command)
                extra = {k: v for k, v in definition.items() if k not in {"type", "command"}}
                name = names.get(key)
                hooks.append(
                    Hook(
                        name=name or f"{harness}-{event}-{group_index}-{hook_index}",
                        event=key[0],
                        matcher=key[1],
                        command=command,
                        harnesses=scopes.get(name, []) if name else [harness],
                        extra=extra,
                    )
                )
    return hooks


# --- MCP servers --------------------------------------------------------------------------------------------------


def _encode_mcp(harness: HarnessLayout, server: MCP) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if server.url:
        if harness.mcp_format == "json":
            data[harness.mcp_type_key] = server.transport
        data["url"] = server.url
    if server.command:
        data["command"] = server.command
    if server.args:
        data["args"] = server.args
    if server.env:
        data["env"] = server.env
    if harness.mcp_format == "toml":
        if server.disabled:
            data["enabled"] = False
    elif harness.mcp_type_key == "transport":  # Claude has no per-server disable/approve keys.
        if server.disabled:
            data["disabled"] = True
        if server.auto_approve:
            data["autoApprove"] = server.auto_approve
    return {**data, **server.extra}


def _decode_mcp(harness: HarnessLayout, name: str, data: dict[str, Any]) -> MCP:
    data = dict(data)
    fields: dict[str, Any] = {"name": name}
    if harness.mcp_format == "toml":
        if "url" in data:
            fields["transport"] = "http"
        if data.pop("enabled", True) is False:
            fields["disabled"] = True
    elif (transport := data.pop(harness.mcp_type_key, None)) is not None:
        fields["transport"] = transport
    for key, attribute in (
        ("url", "url"),
        ("command", "command"),
        ("args", "args"),
        ("env", "env"),
        ("disabled", "disabled"),
        ("autoApprove", "auto_approve"),
    ):
        if key in data:
            fields[attribute] = data.pop(key)
    return MCP(**fields, extra=data)


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_TABLE = re.compile(r"^\s*\[\s*mcp_servers\s*\.\s*(?P<name>\"(?:[^\"\\]|\\.)*\"|[A-Za-z0-9_-]+)")


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
    raise ValueError(f"cannot write {type(value).__name__} to TOML")


def _toml_tables(name: str, data: dict[str, Any]) -> str:
    """``[mcp_servers.<name>]`` plus an ``.env`` subtable, so environments stay readable."""
    header = f"mcp_servers.{_toml_key(name)}"
    lines = [f"[{header}]"] + [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in data.items() if k != "env"]
    if data.get("env"):
        lines += ["", f"[{header}.env]"] + [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in data["env"].items()]
    return "\n".join(lines) + "\n"


def _strip_toml_tables(text: str, names: set[str]) -> str:
    """Remove ``[mcp_servers.<name>]`` tables (and their subtables) for ``names``, keeping everything else."""
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            match = _TABLE.match(line)
            name = json.loads(match["name"]) if match and match["name"].startswith('"') else (match and match["name"])
            skipping = name in names
        if not skipping:
            kept.append(line)
    return "".join(kept)


def _read_mcp_servers(root: Path, harness: HarnessLayout) -> dict[str, Any]:
    path = root / harness.mcp_file if harness.mcp_file else None
    if path is None or not path.is_file():
        return {}
    if harness.mcp_format == "toml":
        return tomllib.loads(path.read_text(encoding="utf-8")).get("mcp_servers", {})
    return _read_json(path).get("mcpServers", {})


def _write_mcp_servers(root: Path, harness: HarnessLayout, servers: dict[str, MCP], remove: set[str]) -> None:
    assert harness.mcp_file is not None
    path = _destination(root, harness.mcp_file)
    if harness.mcp_format == "json":
        data = _read_json(path)
        existing = {name: value for name, value in data.get("mcpServers", {}).items() if name not in remove}
        existing.update({name: _encode_mcp(harness, server) for name, server in servers.items()})
        rest = {key: value for key, value in data.items() if key != "mcpServers"}
        _write_json(root, harness.mcp_file, {**rest, "mcpServers": existing} if existing else rest)
        return
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    text = _strip_toml_tables(text, remove | set(servers)).rstrip()
    tables = [_toml_tables(name, _encode_mcp(harness, server)) for name, server in sorted(servers.items())]
    text = "\n\n".join(part for part in [text, *(table.rstrip() for table in tables)] if part)
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{harness.mcp_file}: MCP servers must be [mcp_servers.<name>] tables ({error})") from None
    if text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    elif path.is_file():
        path.unlink()
        _prune(path.parent, root)


# --- files --------------------------------------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")  # noqa: TRY004 -- malformed input
    return data


def _write_json(root: Path, relative: str, data: dict[str, Any]) -> None:
    path = _destination(root, relative)
    if not data:
        if path.is_file():
            path.unlink()
            _prune(path.parent, root)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _destination(root: Path, relative: str) -> Path:
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents):
        raise ValueError(f"refusing to write through a symlink: {relative}")
    return path


def _prune(directory: Path, root: Path) -> None:
    while directory != root and root in directory.parents and directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
        directory = directory.parent


def _read_record(root: Path) -> dict[str, Any]:
    """The project's install record: ``{"schema_version", "agents": {name: {"context", "harnesses"}}}``."""
    data = _read_json(root / RECORD)
    if not data:
        return {"schema_version": RECORD_SCHEMA_VERSION, "agents": {}}
    if data.get("schema_version") != RECORD_SCHEMA_VERSION:
        raise ValueError(f"unsupported install record: {root / RECORD}")
    return data


def installed_agents(project_dir: str | Path) -> dict[str, dict[str, str | None]]:
    """The agents installed in a project: ``{name: {harness: version}}``."""
    record = _read_record(Path(project_dir).resolve())
    return {name: {harness: entry.get("version") for harness, entry in sorted(agent["harnesses"].items())}
            for name, agent in sorted(record["agents"].items())}


def _planned_files(agent: MetaAgent, harness: HarnessLayout) -> dict[str, str]:
    """Whole files: skills, rules/workflows/commands, this harness's extensions, and hook scripts. Context
    documents are merged separately."""
    files: dict[str, str] = {}
    for name, skill in agent.skills.items():
        directory = f"{harness.location('skills')}/{name}"
        files[f"{directory}/{SKILL_FILE}"] = skill_markdown(name, skill.description, skill.metadata, skill.instructions)
        files.update({f"{directory}/{relative}": content for relative, content in skill.files.items()})
    for resource in agent.resources.values():
        files[f"{harness.location(resource.location)}/{resource.path}"] = resource.content
    for extension in agent.extensions.values():
        if extension.harness == harness.name:
            files[f"{harness.location('extensions')}/{extension.path}"] = extension.content
    if harness.hooks_file is not None:
        for hook in agent.hooks.values():
            if hook.enabled and hook.script and not hook.command:
                files[f"{harness.base}/hooks/{hook.name}.sh"] = hook.script
    return files


def _stamped(path: str, text: str, stamp: str, version: str, harness: HarnessLayout) -> str:
    """An installed file with its stamp; a skill's ``SKILL.md`` also gets ``version`` in its frontmatter."""
    if path.startswith(f"{harness.location('skills')}/") and path.endswith(f"/{SKILL_FILE}"):
        frontmatter, body, _ = split_frontmatter(text)
        if frontmatter is not None:
            data = frontmatter if "version" in frontmatter else {**frontmatter, "version": version}
            return render_frontmatter(data, stamp_file(SKILL_FILE, body, stamp))
    return stamp_file(path, text, stamp)


def _unstamped(text: str, path: str, names: set[str]) -> str:
    for name in names:
        text = unstamp_file(path, text, name)
    return text


# --- installation -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Installation:
    """What an install wrote, relative to the project root.

    ``context`` maps each context document to the sections thearc owns in it, or
    to ``None`` when thearc owns the whole file. ``skipped`` lists resources the
    harness cannot express (e.g. MCP servers for Pi).
    """

    harness: str
    files: tuple[str, ...]
    context: dict[str, tuple[str, ...] | None]
    hooks: tuple[str, ...]
    mcps: tuple[str, ...]
    skipped: tuple[str, ...]
    record: str


def install(
    agent: MetaAgent, harness: Harness | HarnessLayout, project_dir: str | Path, *, force: bool = False,
    _keep_context: bool = False,
) -> Installation:
    """Install ``agent`` into ``project_dir`` for ``harness``, replacing what a previous install owned."""
    harness = get_harness(harness)
    root = Path(project_dir).resolve()
    record = _read_record(root)
    agents: dict[str, Any] = record["agents"]
    mine = agents.setdefault(agent.name, {"context": {}, "harnesses": {}})
    previous = mine["harnesses"].get(harness.name, {})
    owned_files = set(previous.get("files", []))
    owned_context: dict[str, list[str] | None] = mine["context"]
    owned_hook_names: dict[str, list] = previous.get("hooks", {})
    owned_mcps = set(previous.get("mcps", []))

    skipped = [f"hook:{name}" for name, hook in agent.hooks.items()
               if harness.hooks_file is None and hook.installs_into(harness.name)]
    skipped += [f"mcp:{name}" for name in agent.mcps if harness.mcp_file is None]
    skipped += [f"extension:{key}" for key, extension in agent.extensions.items() if extension.harness != harness.name]
    hooks = {name: hook for name, hook in agent.hooks.items()
             if harness.hooks_file and hook.installs_into(harness.name)}
    stamp = label(agent.name, agent.version)
    stamped_by = {agent.name, *agents}  # Stamps to recognize: every installed agent's.
    ours = _Ownership({tuple(key) for key in owned_hook_names.values()}, agent.owns_hooks)
    mcps = agent.mcps if harness.mcp_file else {}
    plain = _planned_files(agent, harness)
    files = ({path: _stamped(path, text, stamp, agent.version or "", harness) for path, text in plain.items()}
             if stamp else plain)
    versioned_skills = sorted(name for name, skill in agent.skills.items()
                              if stamp and "version" not in skill.metadata)
    configuration = {path for path in (harness.hooks_file, harness.mcp_file, RECORD, *agent.context) if path}
    if clash := configuration & files.keys():
        raise ValueError(f"resources collide with harness configuration files: {sorted(clash)}")

    conflicts = [
        relative
        for relative, content in files.items()
        if relative not in owned_files
        and (path := root / relative).is_file()
        and path.read_text(encoding="utf-8") not in {content, plain[relative]}
    ]
    servers = _read_mcp_servers(root, harness)
    conflicts += [
        f"{harness.mcp_file}#{name}"
        for name, server in mcps.items()
        if name in servers and name not in owned_mcps and servers[name] != _encode_mcp(harness, server)
    ]
    hook_data = _read_json(root / harness.hooks_file) if harness.hooks_file else {}
    if harness.hooks_format == "flat":
        conflicts += [
            f"{harness.hooks_file}#{name}"
            for name, hook in hooks.items()
            if name in hook_data and name not in owned_hook_names and hook_data[name] != _flat_entry(hook)
            and not ours(_flat_key(hook_data[name]))
        ]
    if conflicts and not force:
        raise InstallConflict(f"would overwrite files or entries not installed by thearc: {sorted(conflicts)}")

    for relative in sorted(owned_files - files.keys()):
        if (stale := _destination(root, relative)).is_file():
            stale.unlink()
            _prune(stale.parent, root)
    for relative, content in files.items():
        destination = _destination(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        if relative.startswith(f"{harness.base}/hooks/") and relative.endswith(".sh"):
            os.chmod(destination, 0o755)

    context = owned_context if _keep_context else _install_context(root, agent, owned_context, stamp, stamped_by)

    hook_keys: dict[str, list] = {}
    if harness.hooks_file is not None:
        if harness.hooks_format == "nested":
            data, hook_keys = _merge_nested_hooks(hook_data, hooks, harness, ours)
        else:
            data = {
                name: value
                for name, value in hook_data.items()
                if name not in owned_hook_names and not ours(_flat_key(value))
            }
            data.update({name: _flat_entry(hook) for name, hook in hooks.items()})
            hook_keys = {name: [hook.event, hook.matcher, _hook_command(harness, hook)] for name, hook in hooks.items()}
        _write_json(root, harness.hooks_file, data)
    if harness.mcp_file is not None:
        _write_mcp_servers(root, harness, mcps, owned_mcps | (set(servers) & set(mcps) if force else set()))

    mine["context"] = context
    mine["harnesses"][harness.name] = {
        "version": agent.version,
        "revision": agent.revision,
        "versioned_skills": versioned_skills,
        "files": sorted(files),
        "hooks": hook_keys,
        "hook_harnesses": {name: hooks[name].harnesses for name in sorted(hook_keys) if hooks[name].harnesses},
        "owns_hooks": agent.owns_hooks,
        "mcps": sorted(mcps),
    }
    _take_over(root, agents, agent.name, harness.name)
    _write_json(root, RECORD, {**record, "agents": {name: agents[name] for name in sorted(agents)}})
    return Installation(
        harness.name,
        tuple(sorted(files)),
        {name: None if titles is None else tuple(titles) for name, titles in context.items()},
        tuple(sorted(hook_keys)),
        tuple(sorted(mcps)),
        tuple(skipped),
        RECORD,
    )


def uninstall(harness: Harness | HarnessLayout, project_dir: str | Path, *, name: str | None = None) -> None:
    """Remove what an agent installed for ``harness``: its files, hooks, and MCP servers, and its context
    sections once no other harness has the agent installed. ``name`` picks the agent when several are."""
    harness = get_harness(harness)
    root = Path(project_dir).resolve()
    owner = _owner(_read_record(root)["agents"], harness.name, name)
    if owner is None:
        return
    shared = len(_read_record(root)["agents"][owner]["harnesses"]) > 1  # Other harnesses still read its context.
    install(MetaAgent(name=owner), harness, root, _keep_context=shared)
    record = _read_record(root)
    entry = record["agents"][owner]
    del entry["harnesses"][harness.name]
    if not entry["harnesses"]:
        del record["agents"][owner]
    _write_json(root, RECORD, record if record["agents"] else {})


def _owner(agents: dict[str, Any], harness: str, name: str | None) -> str | None:
    """The agent installed for ``harness``: ``name`` if it is, else the only one; None when there is none."""
    installed = [agent for agent, entry in agents.items() if harness in entry["harnesses"]]
    if name in installed:
        return name
    if len(installed) > 1:
        raise ValueError(f"several agents are installed for {harness} ({sorted(installed)}); pass name=")
    return installed[0] if installed else None


def _take_over(root: Path, agents: dict[str, Any], name: str, harness: str) -> None:
    """Remove from other agents' records what agent ``name`` now owns: files and hook/MCP entries in
    ``harness``, and context sections (a whole document another agent owned keeps its other sections)."""
    mine = agents[name]
    installed = mine["harnesses"][harness]
    for other, entry in list(agents.items()):
        if other == name:
            continue
        theirs = entry["harnesses"].get(harness)
        if theirs is not None:
            theirs["files"] = [path for path in theirs["files"] if path not in installed["files"]]
            mine_keys = [tuple(key) for key in installed["hooks"].values()]
            theirs["hooks"] = {hook: key for hook, key in theirs["hooks"].items() if tuple(key) not in mine_keys}
            theirs["mcps"] = [server for server in theirs["mcps"] if server not in installed["mcps"]]
        for filename, titles in list(entry["context"].items()):
            if filename not in mine["context"]:
                continue
            ours = mine["context"][filename]
            if ours is None:
                del entry["context"][filename]
                continue
            if titles is None:  # Their whole document now also holds our sections: they keep the rest.
                titles = [title for title, _ in top_sections((root / filename).read_text(encoding="utf-8"))]
            taken = {title.casefold() for title in ours}
            entry["context"][filename] = [title for title in titles if title.casefold() not in taken]


def _install_context(
    root: Path, agent: MetaAgent, owned: dict[str, list[str] | None], stamp: str | None = None,
    stamped_by: set[str] = frozenset(),  # type: ignore[assignment]
) -> dict[str, list[str] | None]:
    """Write or merge context documents; returns the new ownership (``None`` = whole file).

    Stamps already in a document are removed before merging (a stamped heading would not match its
    title), and the installed sections are stamped again afterwards.
    """

    def existing(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        for name in stamped_by:
            text = unstamp_markdown(text, name)
        return text

    result: dict[str, list[str] | None] = {}
    for filename in sorted(set(owned) - set(agent.context)):
        path = _destination(root, filename)
        if not path.is_file():
            continue
        if owned[filename] is None:
            path.unlink()
            _prune(path.parent, root)
        else:
            path.write_text(replace_sections(existing(path), [], remove=tuple(owned[filename] or ())),
                            encoding="utf-8")
    for filename, document in sorted(agent.context.items()):
        path = _destination(root, filename)
        if not path.is_file() or (filename in owned and owned[filename] is None):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.write(stamp_markdown(document.content, stamp) if stamp else document.content)
            result[filename] = None
            continue
        sections = top_sections(document.content)
        titles = [title for title, _ in sections]
        previous = [
            title for title in owned.get(filename) or [] if title.casefold() not in {t.casefold() for t in titles}
        ]
        merged = replace_sections(existing(path), sections, remove=tuple(previous))
        if stamp:
            merged = stamp_markdown(merged, stamp, titles=set(titles))
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(merged)
        result[filename] = titles
    return result


# --- import -------------------------------------------------------------------------------------------------------


def _text_files(directory: Path) -> Iterable[tuple[str, str]]:
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            try:
                yield path.relative_to(directory).as_posix(), path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue


def read_installation(
    harness: Harness | HarnessLayout, project_dir: str | Path, *, name: str | None = None, version: str | None = None
) -> MetaAgent:
    """Import the configuration installed for ``harness`` in ``project_dir`` (by thearc or by hand).

    For a context document thearc merged into, only the sections it owns are imported. When several
    agents are installed for ``harness``, ``name`` picks one; for a project thearc did not install
    into, ``name`` and ``version`` name the imported agent.
    """
    harness = get_harness(harness)
    root = Path(project_dir).resolve()
    agents = _read_record(root)["agents"]
    stamp_names = set(agents)
    owner = _owner(agents, harness.name, name)
    marker: dict[str, Any] = {**agents[owner]["harnesses"][harness.name], "name": owner,
                              "context": agents[owner]["context"]} if owner else {}
    versioned = set(marker.get("versioned_skills", []))

    skills = []
    skills_dir = root / harness.location("skills")
    if skills_dir.is_dir():
        for directory in sorted(path for path in skills_dir.iterdir() if (path / SKILL_FILE).is_file()):
            frontmatter, body, _ = split_frontmatter((directory / SKILL_FILE).read_text(encoding="utf-8"))
            metadata = dict(frontmatter or {})
            metadata.pop("name", None)
            if directory.name in versioned:
                metadata.pop("version", None)
            body = _unstamped(body, SKILL_FILE, stamp_names)
            files = {relative: _unstamped(text, relative, stamp_names)
                     for relative, text in _text_files(directory) if relative != SKILL_FILE}
            skills.append(
                Skill(
                    name=directory.name,
                    description=metadata.pop("description", ""),
                    instructions=body,
                    metadata=metadata,
                    files=files,
                )
            )
    resources = [
        AgentResource(location=location, path=relative, content=_unstamped(text, relative, stamp_names))
        for location in ("rules", "workflows", "commands")
        for relative, text in _text_files(root / harness.location(location))
    ]
    extensions = [
        Extension(harness=harness.name, path=relative, content=_unstamped(text, relative, stamp_names))
        for relative, text in _text_files(root / harness.location("extensions"))
    ]

    owned_context = marker.get("context")
    context = []
    for filename in sorted(owned_context) if owned_context is not None else CONTEXT_FILES:
        path = root / filename
        if not path.is_file():
            continue
        text = _unstamped(path.read_text(encoding="utf-8"), filename, stamp_names)
        titles = owned_context.get(filename) if owned_context is not None else None
        if titles is not None:
            text = "\n".join(section for title in titles if (section := section_text(text, title)) is not None)
        context.append(ContextDocument(filename=filename, content=text))

    hooks: list[Hook] = []
    if harness.hooks_file is not None:
        data = _read_json(root / harness.hooks_file)
        if harness.hooks_format == "nested":
            names = {tuple(key): n for n, key in marker.get("hooks", {}).items()}
            hooks = _read_nested_hooks(data, names, harness.name, marker.get("hook_harnesses", {}))
        else:  # Hooks thearc installed keep their recorded scope; others belong to this harness.
            owned, scopes = marker.get("hooks", {}), marker.get("hook_harnesses", {})
            hooks = [
                Hook(name=hook_name, harnesses=scopes.get(hook_name, []) if hook_name in owned else [harness.name],
                     **entry)
                for hook_name, entry in data.items()
            ]
    mcps = [_decode_mcp(harness, server, data) for server, data in _read_mcp_servers(root, harness).items()]
    return MetaAgent.model_validate(
        {
            "name": name or marker.get("name") or f"{harness.name}-project",
            "version": version if version is not None else marker.get("version"),
            "owns_hooks": marker.get("owns_hooks", []),
            "skills": skills,
            "hooks": hooks,
            "mcps": mcps,
            "context": context,
            "resources": resources,
            "extensions": extensions,
        }
    )
