"""MetaAgent: the portable, harness-independent configuration of an agent, and its journal."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from thearc.harnesses import Harness
from thearc.resources.journal import Journal, Rated, Reflected
from thearc.resources.targets import ResourceTarget, safe_name, safe_relative_path, target_pattern

if TYPE_CHECKING:
    from thearc.reflection import Reflection
    from thearc.resources.drift import Drift
    from thearc.resources.files import AgentFiles
    from thearc.resources.harness import HarnessLayout, Installation
    from thearc.resources.ratings import RatingPolicy

SKILL_FILE = "SKILL.md"
RATINGS_FILE = "RATINGS.md"  # Written by release(include_scales=True); a view, not configuration.
_RESERVED_SKILL_METADATA = frozenset({"name", "description"})


class Skill(BaseModel):
    """Reusable instructions, metadata, and supporting files for a capability.

    ``instructions`` is the exact ``SKILL.md`` body after the frontmatter;
    ``metadata`` holds frontmatter fields other than ``name`` and ``description``.
    """

    model_config = {"extra": "forbid"}
    name: str
    description: str = ""
    instructions: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return safe_name(value)

    @field_validator("files")
    @classmethod
    def _files(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value:
            if safe_relative_path(path) == SKILL_FILE:
                raise ValueError(f"{SKILL_FILE} is the skill's instructions, not a bundled file")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if reserved := _RESERVED_SKILL_METADATA & value.keys():
            raise ValueError(f"metadata cannot redefine {sorted(reserved)}")
        return value


class Hook(BaseModel):
    """An action the harness runs on an event.

    ``harnesses`` limits the hook to those harnesses (matchers name a harness's own tools, so
    hooks often differ per harness); empty means every harness that has hooks.
    """

    model_config = {"extra": "forbid"}
    name: str
    event: str = "pre_tool_use"
    command: str | None = None
    script: str | None = None
    matcher: str | None = None
    description: str = ""
    enabled: bool = True
    harnesses: list[Harness] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return safe_name(value)

    def installs_into(self, harness: str) -> bool:
        return self.enabled and (not self.harnesses or harness in self.harnesses)


class MCP(BaseModel):
    """A connection to a Model Context Protocol server."""

    model_config = {"extra": "forbid"}
    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    transport: str = "stdio"
    disabled: bool = False
    auto_approve: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return safe_name(value)


class ContextDocument(BaseModel):
    """Always-loaded instructions such as ``AGENTS.md`` or ``CLAUDE.md``."""

    model_config = {"extra": "forbid"}
    filename: str
    content: str

    @field_validator("filename")
    @classmethod
    def _filename(cls, value: str) -> str:
        return safe_relative_path(value)


class AgentResource(BaseModel):
    """A harness-specific text file: a rule, workflow, or command."""

    model_config = {"extra": "forbid"}
    location: Literal["rules", "workflows", "commands"]
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return safe_relative_path(value)

    @property
    def key(self) -> str:
        return f"{self.location}/{self.path}"

    @property
    def kind(self) -> Literal["rule", "workflow", "command"]:
        return self.location[:-1]  # type: ignore[return-value]


class Extension(BaseModel):
    """A file only one harness understands, installed only there — e.g. a Pi extension (``.pi/extensions/x.ts``)
    that adds behaviour Pi has no hooks for. Other harnesses skip it."""

    model_config = {"extra": "forbid"}
    harness: Harness
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return safe_relative_path(value)

    @property
    def key(self) -> str:
        return f"{self.harness}/{self.path}"


Resource = Skill | Hook | MCP | ContextDocument | AgentResource | Extension


class MetaAgent(BaseModel):
    """A named, optionally versioned agent configuration and its journal.

    The journal travels with the MetaAgent but is not configuration: it never
    changes the ``revision``. Nothing here installs or executes a resource.

    ``owns_hooks`` are glob patterns of hook commands (e.g. ``"graphify *"``) that belong
    to this MetaAgent wherever they appear: installing removes every matching hook from
    the harness's configuration before adding the MetaAgent's own, so hooks installed by
    hand or by an older tool are replaced rather than duplicated.
    """

    model_config = {"extra": "forbid", "validate_assignment": True}
    name: str
    version: str | None = None
    owns_hooks: list[str] = Field(default_factory=list)
    frozen: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)
    skills: dict[str, Skill] = Field(default_factory=dict)
    hooks: dict[str, Hook] = Field(default_factory=dict)
    mcps: dict[str, MCP] = Field(default_factory=dict)
    context: dict[str, ContextDocument] = Field(default_factory=dict)
    resources: dict[str, AgentResource] = Field(default_factory=dict)
    extensions: dict[str, Extension] = Field(default_factory=dict)
    journal: Journal = Field(default_factory=Journal)
    _workspace: Path | None = PrivateAttr(default=None)
    # Lineage IDs read from workspace markers, by target; they tell sections apart across a rename.
    _identities: dict[ResourceTarget, str] = PrivateAttr(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return safe_name(value)

    @field_validator("version")
    @classmethod
    def _version(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("version must be a non-blank, trimmed, single-line label")
        return value

    @field_validator("frozen", "ignore")
    @classmethod
    def _patterns(cls, value: list[str]) -> list[str]:
        return sorted({target_pattern(pattern) for pattern in value})

    def is_frozen(self, target: ResourceTarget) -> bool:
        """Whether evolution must leave ``target`` as it is."""
        return any(target.matches(pattern) for pattern in self.frozen)

    def is_ignored(self, target: ResourceTarget) -> bool:
        """Whether ``target`` is left out of this configuration."""
        return any(target.matches(pattern) for pattern in self.ignore)

    @field_validator("skills", "hooks", "mcps", "context", "resources", "extensions", mode="before")
    @classmethod
    def _normalize_collection(cls, value: Any, info: Any) -> dict:
        """Accept named mappings or lists; keys must equal each item's identity."""
        field = info.field_name
        if isinstance(value, list):
            items = [(None, item) for item in value]
        elif isinstance(value, dict):
            items = list(value.items())
        else:
            raise ValueError(f"{field} must be a mapping or list")  # noqa: TRY004 -- Pydantic validation error
        normalized = {}
        for key, item in items:
            data = item.model_dump() if isinstance(item, BaseModel) else item
            if not isinstance(data, dict):
                raise ValueError(f"invalid {field} entry: {key}")  # noqa: TRY004 -- Pydantic validation error
            identity: Any
            if field in {"resources", "extensions"}:
                owner = "location" if field == "resources" else "harness"
                if owner not in data or "path" not in data:
                    raise ValueError(f"{field} entries require {owner} and path")
                identity = f"{data[owner]}/{data['path']}"
            else:
                attribute = "filename" if field == "context" else "name"
                identity = data.get(attribute, key)
                if identity is None:
                    raise ValueError(f"{field} entries require {attribute}")
                data = {**data, attribute: identity}
            if key is not None and key != identity:
                raise ValueError(f"{field} key {key!r} does not match identity {identity!r}")
            if identity in normalized:
                raise ValueError(f"duplicate {field} entry: {identity}")
            normalized[identity] = data
        return normalized

    def resource(self, target: ResourceTarget) -> Resource | str:
        """The resource a target belongs to; a skill file is returned as its text."""
        if target.kind == "skill_file":
            skill, _, path = target.name.partition("/")
            return self.skills[skill].files[path]
        if target.kind in {"rule", "workflow", "command"}:
            return self.resources[f"{target.kind}s/{target.name}"]
        if target.kind == "extension":
            return self.extensions[target.name]
        collections: dict[str, dict[str, Any]] = {
            "skill": self.skills,
            "hook": self.hooks,
            "mcp": self.mcps,
            "context": self.context,
        }
        return collections[target.kind][target.name]

    def files(self) -> AgentFiles:
        """The configuration as line-addressable files, as reflectors read and cite them."""
        from thearc.resources.files import AgentFiles

        return AgentFiles.from_agent(self)

    @property
    def revision(self) -> str:
        """``version@rev_…``: the version, if any, and the content hash of the configuration's files.

        The hash part (``AgentFiles.revision``) depends on the files only, never on the name or
        journal; the version prefix tells revisions of different releases apart.
        """
        content = self.files().revision
        return f"{self.version}@{content}" if self.version else content

    def diff(self, other: MetaAgent, *, context_lines: int = 3) -> str:
        """Unified diff of the configuration files from this agent to ``other``; empty means equal."""
        from thearc.resources.files import diff_files

        return diff_files(self.files().files, other.files().files, context_lines=context_lines)

    @classmethod
    def from_workspace(cls, path: str | Path, *, journal: bool = True, **limits: int) -> MetaAgent:
        """The agent saved in a workspace, which it remembers; ``journal=False`` reads only its configuration."""
        from thearc.resources.workspace import read_workspace

        return read_workspace(path, journal=journal, **limits)

    def reload(self) -> MetaAgent:
        """This agent's workspace as it is now (e.g. after a curator moved its worktree forward)."""
        if self._workspace is None:
            raise ValueError("reload needs an agent read from or initialized in a workspace")
        return type(self).from_workspace(self._workspace)

    def __eq__(self, other: object) -> bool:
        """Equal configuration, identity and journal; where an agent was read from does not count."""
        if not isinstance(other, MetaAgent):
            return NotImplemented
        return all(getattr(self, name) == getattr(other, name) for name in type(self).model_fields)

    __hash__ = None  # type: ignore[assignment]  # Mutable, like any BaseModel.

    @property
    def workspace(self) -> Path | None:
        """The workspace this agent was read from or initialized in, whose journal ``add_reflection`` appends to."""
        return self._workspace

    @property
    def identities(self) -> dict[ResourceTarget, str]:
        """Lineage IDs read from the markers of the workspace this agent was read from, by target."""
        return dict(self._identities)

    def checked_workspace(self, action: str) -> Path:
        """This agent's workspace, which must still hold this configuration (nobody changed it meanwhile)."""
        from thearc.resources.workspace import read_workspace

        if self._workspace is None:
            raise ValueError(f"{action} needs an agent read from or initialized in a workspace")
        if read_workspace(self._workspace, journal=False).revision != self.revision:
            raise ValueError(f"workspace {self._workspace} holds another configuration than this agent")
        return self._workspace

    def add_reflection(self, reflection: Reflection) -> tuple[Rated, ...]:
        """Record a reflection's ratings: each item, once per target its reference resolves to; and keep the whole
        reflection in the journal (``journal.reflections``).

        The reflection must have judged this agent's revision; ratings of frozen targets are not recorded.
        Entry IDs derive from the reflection, so adding it again records nothing new. With a workspace, the new entries
        are also appended to its ``journal.jsonl`` (without reading it); nothing is committed.
        Returns the entries recorded now.
        """
        from thearc.resources.reflection import reflection_entries
        from thearc.resources.workspace import JOURNAL_FILE

        if reflection.revision != self.revision:
            raise ValueError(f"reflection {reflection.id} judged {reflection.revision}, not {self.revision}")
        if self._workspace is not None:
            self.checked_workspace("add_reflection")
        rated = [entry for entry in reflection_entries(reflection, self.files()) if not self.is_frozen(entry.target)]
        new = self.journal.merge(*rated)
        whole = Reflected.model_validate(reflection.to_dict())
        kept = self.journal.record_reflection(whole)
        if self._workspace is not None:
            Journal.append(self._workspace / JOURNAL_FILE, [*new, *([whole] if kept else [])])
        return tuple(entry for entry in new if isinstance(entry, Rated))

    def release(
        self,
        version: str | None = None,
        *,
        part: Literal["major", "minor", "patch"] = "patch",
        branch: str | None = None,
        message: str | None = None,
        include_scales: bool = False,
        policy: RatingPolicy | None = None,
        push: str | None = None,
    ) -> MetaAgent:
        """Version this agent's workspace: set ``version`` (default: bump ``part`` of the current one), commit
        it, and point a new branch (default ``release/<version>``, after the workspace's branch prefix) at that
        commit.

        Only ``agent.json`` changes, so the configuration's content and journal stay as they are; the
        revision changes with the version. With ``include_scales``, ``RATINGS.md`` is written next to
        the configuration: every section with its lineage ID and its ratings on every scale the
        journal uses (``agent_outline``); it is not configuration, so it changes no revision. Pending
        journal lines are committed first. The current branch moves forward with the release commit.
        With ``push``, the release branch is pushed to that remote. Returns the agent at the new version.
        """
        import json

        from thearc.resources.git import GitError, _git, branch_name, commit, commit_journal
        from thearc.resources.ratings import scales_from_ratings, since_last_modification
        from thearc.resources.views import agent_outline
        from thearc.resources.workspace import MANIFEST

        root = self.checked_workspace("release")
        version = version or bump_version(self.version, part)
        branch = branch or branch_name(root, "release", version)
        try:
            _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        except GitError:
            pass
        else:
            raise ValueError(f"branch {branch} already exists")
        commit_journal(root)
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        manifest["version"] = version
        (root / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _git(root, "add", MANIFEST)
        if include_scales and (scales := scales_from_ratings(self.journal.ratings())):
            released = self.model_copy(update={"version": version})
            outline = agent_outline(released, scales, ids=True, policy=policy or since_last_modification)
            (root / RATINGS_FILE).write_text(outline, encoding="utf-8")
            _git(root, "add", RATINGS_FILE)
        sha = commit(root, message or f"Release {self.name} {version}")
        _git(root, "branch", branch, sha)
        if push is not None:
            _git(root, "push", "--quiet", push, f"refs/heads/{branch}:refs/heads/{branch}")
        return MetaAgent.from_workspace(root)

    def to_worktree(self, path: str | Path, *, branch: str | None = None) -> MetaAgent:
        """A new git worktree of this agent's workspace at ``path``, on ``branch`` from its current commit
        (default ``candidate/<folder name>``, after the workspace's branch prefix).

        Pending journal lines are committed first, so the worktree starts with them. Parallel
        pipelines each work on their own worktree from the same commit — their shared base —
        and a merger combines them. The returned agent is bound to the new worktree.
        """
        from thearc.resources.git import _git, branch_name, commit_journal
        from thearc.resources.workspace import _root

        root = self.checked_workspace("to_worktree")
        commit_journal(root)
        if dirty := _git(root, "status", "--porcelain").decode().strip():
            raise ValueError(f"commit or undo the workspace's pending edits first: {dirty.splitlines()}")
        target = _root(path)
        branch = branch or branch_name(root, "candidate", target.name)
        _git(root, "worktree", "add", "-q", "-b", branch, str(target), "HEAD")
        return MetaAgent.from_workspace(target)

    def to_workspace(self, path: str | Path, *, journal: bool = True, overwrite: bool = False) -> Path:
        from thearc.resources.workspace import write_workspace

        return write_workspace(self, path, journal=journal, overwrite=overwrite)

    def init_workspace(
        self, path: str | Path, *, branch: str = "main", reason: str = "Initialize MetaAgent workspace",
        repository: str | Path | None = None, prefix: str = "",
    ) -> MetaAgent:
        """Create a git workspace, or resume a matching one, returning its agent and journal.

        The receiver is unchanged and must have a version. New workspaces seed an empty journal with Added
        entries and commit the configuration. Existing workspaces must match this
        agent's identity and revision; their saved journal is authoritative. With ``repository``, the workspace
        lives in that repository on ``<prefix>`` branches, checked out at ``path`` as a worktree.
        """
        from thearc.resources.workspace import init_workspace

        return init_workspace(self, path, branch=branch, reason=reason, repository=repository, prefix=prefix)

    def install(
        self, harness: Harness | HarnessLayout, project_dir: str | Path, *, force: bool = False
    ) -> Installation:
        """Install into a harness's project layout; see ``thearc.resources.harness``."""
        from thearc.resources.harness import install

        return install(self, harness, project_dir, force=force)

    @classmethod
    def from_installation(
        cls,
        harness: Harness | HarnessLayout,
        project_dir: str | Path,
        *,
        name: str | None = None,
        version: str | None = None,
        ignore: Iterable[str] = (),
    ) -> MetaAgent:
        """Import the configuration installed for ``harness`` in ``project_dir`` (by thearc or by hand), as an agent
        not bound to a workspace. For a context document thearc merged into, only the sections it owns are
        imported. When several agents are installed for ``harness``, ``name`` picks one; for a project thearc did
        not install into, ``name`` and ``version`` name the imported agent. Targets ``ignore`` names (patterns, see
        ``MetaAgent.ignore``) are left out."""
        from thearc.resources.harness import read_installation

        return read_installation(harness, project_dir, name=name, version=version, ignore=ignore)

    def ignore_targets(self, patterns: Iterable[str], *, remove: bool = False, reason: str | None = None,
                       author: str = "user") -> MetaAgent:
        """Leave the targets ``patterns`` name out of this workspace's configuration from now on (or, with ``remove``,
        stop ignoring them), as one commit recorded in the journal; returns the agent as committed.

        Ignored targets are removed from the workspace with Removed entries; installing then leaves their files
        alone. Un-ignoring only drops the patterns: the targets come back when the installation is imported again.
        """
        from thearc.resources.git import commit_configuration
        from thearc.resources.workspace import read_workspace

        root = self.checked_workspace("ignore_targets")
        patterns = list(patterns)
        if remove:
            updated = self.model_copy(update={"ignore": [p for p in self.ignore if p not in patterns]})
            message = reason or "Stop ignoring " + ", ".join(patterns)
        else:
            updated = self.model_copy(update={"ignore": [*self.ignore, *patterns]}).without_ignored()
            message = reason or "Ignore " + ", ".join(patterns)
        updated._workspace = root
        commit_configuration(updated, message, author=author)
        return read_workspace(root)

    def sync_installation(self, project_dir: str | Path, harnesses: Sequence[Harness], *, author: str = "user",
                          message: str | None = None) -> Drift:
        """Commit to this workspace the edits made by hand to its installation in ``project_dir`` for ``harnesses``
        since it was installed, as one change by ``author`` (see ``thearc.resources.drift``)."""
        from thearc.resources.drift import sync_installation

        return sync_installation(self, project_dir, harnesses, author=author, message=message)

    def without_ignored(self) -> MetaAgent:
        """A copy without the targets ``ignore`` names: dropped whole, or cut out of their Markdown."""
        from thearc.resources.ignore import without_ignored

        return without_ignored(self)


def bump_version(version: str | None, part: Literal["major", "minor", "patch"] = "patch") -> str:
    """``1.4.2`` → ``2.0.0`` / ``1.5.0`` / ``1.4.3``; missing parts count as 0 (``1`` → ``1.1.0``)."""
    if version is None:
        raise ValueError("the agent has no version to bump; pass one")
    numbers = version.split(".")
    if not 1 <= len(numbers) <= 3 or not all(n.isdigit() for n in numbers):
        raise ValueError(f"cannot bump {version!r}; pass the new version explicitly")
    major, minor, patch = ([int(n) for n in numbers] + [0, 0])[:3]
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"

