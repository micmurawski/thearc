"""MetaAgent: the portable, harness-independent configuration of an agent, and its journal."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from thearcv2.harnesses import Harness
from thearcv2.resources.journal import Journal, Rated
from thearcv2.resources.ratings import RatingPolicy, RatingScale
from thearcv2.resources.targets import ResourceTarget, safe_name, safe_relative_path

if TYPE_CHECKING:
    from thearcv2.reflection import Reflection
    from thearcv2.resources.files import AgentFiles
    from thearcv2.resources.harness import HarnessLayout, Installation

SKILL_FILE = "SKILL.md"
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
        from thearcv2.resources.files import AgentFiles

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
        from thearcv2.resources.files import diff_files

        return diff_files(self.files().files, other.files().files, context_lines=context_lines)

    @classmethod
    def from_workspace(cls, path: str | Path, *, journal: bool = True, **limits: int) -> MetaAgent:
        """The agent saved in a workspace; ``journal=False`` reads only its configuration."""
        from thearcv2.resources.workspace import read_workspace

        return read_workspace(path, journal=journal, **limits)

    def __eq__(self, other: object) -> bool:
        """Equal configuration, identity and journal; where an agent was read from does not count."""
        if not isinstance(other, MetaAgent):
            return NotImplemented
        return all(getattr(self, name) == getattr(other, name) for name in type(self).model_fields)

    __hash__ = None  # type: ignore[assignment]  # Mutable, like any BaseModel.

    @property
    def identities(self) -> dict[ResourceTarget, str]:
        """Lineage IDs read from the markers of the workspace this agent was read from, by target."""
        return dict(self._identities)

    def checked_workspace(self, action: str) -> Path:
        """This agent's workspace, which must still hold this configuration (nobody changed it meanwhile)."""
        from thearcv2.resources.workspace import read_workspace

        if self._workspace is None:
            raise ValueError(f"{action} needs an agent read from or initialized in a workspace")
        if read_workspace(self._workspace, journal=False).revision != self.revision:
            raise ValueError(f"workspace {self._workspace} holds another configuration than this agent")
        return self._workspace

    @property
    def workspace(self) -> Path | None:
        """The workspace this agent was read from or initialized in, whose journal ``add_reflection`` appends to."""
        return self._workspace

    def annotate(
        self,
        scales: RatingScale | Sequence[RatingScale],
        *,
        policy: RatingPolicy | None = None,
        message: str = "Refresh ratings",
    ) -> str | None:
        """Refresh the rating summaries in the workspace's markers from the journal, and commit them.

        ``policy`` defaults to ``since_last_modification``: ratings of each target's current
        text. Only markers change, so the commit records no journal entries. Returns the
        commit, or None when the markers were already current.
        """
        from thearcv2.resources.git import _git, commit
        from thearcv2.resources.ratings import since_last_modification
        from thearcv2.resources.workspace import JOURNAL_FILE, write_workspace

        root = self.checked_workspace("annotate")
        scales = (scales,) if isinstance(scales, RatingScale) else tuple(scales)
        policy = policy or since_last_modification
        write_workspace(self, root, journal=False, overwrite=True, scales=scales, policy=policy)
        _git(root, "add", "-A", "--", ".", f":!{JOURNAL_FILE}")
        if not _git(root, "diff", "--cached", "--name-only").strip():
            return None
        return commit(root, message)

    def add_reflection(self, reflection: Reflection) -> tuple[Rated, ...]:
        """Record a reflection's ratings: each item, once per target its reference resolves to.

        The reflection must have judged this agent's revision. Entry IDs derive from the
        reflection, so adding it again records nothing new. With a workspace, the new entries
        are also appended to its ``journal.jsonl`` (without reading it); nothing is committed.
        Returns the entries recorded now.
        """
        from thearcv2.resources.reflection import reflection_entries
        from thearcv2.resources.workspace import JOURNAL_FILE

        if reflection.revision != self.revision:
            raise ValueError(f"reflection {reflection.id} judged {reflection.revision}, not {self.revision}")
        if self._workspace is not None:
            self.checked_workspace("add_reflection")
        new = self.journal.merge(*reflection_entries(reflection, self.files()))
        if self._workspace is not None:
            Journal.append(self._workspace / JOURNAL_FILE, new)
        return tuple(entry for entry in new if isinstance(entry, Rated))

    def to_workspace(self, path: str | Path, *, journal: bool = True, overwrite: bool = False) -> Path:
        from thearcv2.resources.workspace import write_workspace

        return write_workspace(self, path, journal=journal, overwrite=overwrite)

    def init_workspace(
        self, path: str | Path, *, branch: str = "main", reason: str = "Initialize MetaAgent workspace"
    ) -> MetaAgent:
        """Create a git workspace, or resume a matching one, returning its agent and journal.

        The receiver is unchanged and must have a version. New workspaces seed an empty journal with Added
        entries and commit the configuration. Existing workspaces must match this
        agent's identity and revision; their saved journal is authoritative.
        """
        from thearcv2.resources.workspace import init_workspace

        return init_workspace(self, path, branch=branch, reason=reason)

    def install(
        self, harness: Harness | HarnessLayout, project_dir: str | Path, *, force: bool = False
    ) -> Installation:
        """Install into a harness's project layout; see ``thearcv2.resources.harness``."""
        from thearcv2.resources.harness import install

        return install(self, harness, project_dir, force=force)

    @classmethod
    def from_installation(
        cls,
        harness: Harness | HarnessLayout,
        project_dir: str | Path,
        *,
        name: str | None = None,
        version: str | None = None,
    ) -> MetaAgent:
        """Import the configuration installed for a harness in ``project_dir``."""
        from thearcv2.resources.harness import read_installation

        return read_installation(harness, project_dir, name=name, version=version)
