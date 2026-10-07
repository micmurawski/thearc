"""MetaAgent: the portable, harness-independent configuration of an agent, and its journal."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator

from thearcv2.resources.journal import Journal
from thearcv2.resources.targets import ResourceTarget, safe_name, safe_relative_path

if TYPE_CHECKING:
    from thearcv2.harnesses import Harness
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
    """An action the harness runs on an event."""

    model_config = {"extra": "forbid"}
    name: str
    event: str = "pre_tool_use"
    command: str | None = None
    script: str | None = None
    matcher: str | None = None
    description: str = ""
    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return safe_name(value)


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


Resource = Skill | Hook | MCP | ContextDocument | AgentResource


class MetaAgent(BaseModel):
    """A named, optionally versioned agent configuration and its journal.

    The journal travels with the MetaAgent but is not configuration: it never
    changes the ``revision``. Nothing here installs or executes a resource.
    """

    model_config = {"extra": "forbid", "validate_assignment": True}
    name: str
    version: str | None = None
    skills: dict[str, Skill] = Field(default_factory=dict)
    hooks: dict[str, Hook] = Field(default_factory=dict)
    mcps: dict[str, MCP] = Field(default_factory=dict)
    context: dict[str, ContextDocument] = Field(default_factory=dict)
    resources: dict[str, AgentResource] = Field(default_factory=dict)
    journal: Journal = Field(default_factory=Journal)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return safe_name(value)

    @field_validator("version")
    @classmethod
    def _version(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()
                                  or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError("version must be a non-blank, trimmed, single-line label")
        return value

    @field_validator("skills", "hooks", "mcps", "context", "resources", mode="before")
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
            if field == "resources":
                if "location" not in data or "path" not in data:
                    raise ValueError("resource entries require location and path")
                identity = f'{data["location"]}/{data["path"]}'
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
        collection = {"skill": self.skills, "hook": self.hooks, "mcp": self.mcps, "context": self.context}
        return collection[target.kind][target.name]

    def files(self) -> AgentFiles:
        """The configuration as line-addressable files, as reflectors read and cite them."""
        from thearcv2.resources.files import AgentFiles

        return AgentFiles.from_agent(self)

    @property
    def revision(self) -> str:
        """Content hash of the configuration's files; excludes identity and journal."""
        return self.files().revision

    def diff(self, other: MetaAgent, *, context_lines: int = 3) -> str:
        """Unified diff of the configuration files from this agent to ``other``; empty means equal."""
        from thearcv2.resources.files import diff_files

        return diff_files(self.files().files, other.files().files, context_lines=context_lines)

    @classmethod
    def from_workspace(cls, path: str | Path, **limits: int) -> MetaAgent:
        from thearcv2.resources.workspace import read_workspace

        return read_workspace(path, **limits)

    def to_workspace(self, path: str | Path, *, journal: bool = True, overwrite: bool = False) -> Path:
        from thearcv2.resources.workspace import write_workspace

        return write_workspace(self, path, journal=journal, overwrite=overwrite)

    def install(self, harness: Harness | HarnessLayout, project_dir: str | Path, *,
                force: bool = False) -> Installation:
        """Install into a harness's project layout; see ``thearcv2.resources.harness``."""
        from thearcv2.resources.harness import install

        return install(self, harness, project_dir, force=force)

    @classmethod
    def from_installation(cls, harness: Harness | HarnessLayout, project_dir: str | Path, *,
                          name: str | None = None, version: str | None = None) -> MetaAgent:
        """Import the configuration installed for a harness in ``project_dir``."""
        from thearcv2.resources.harness import read_installation

        return read_installation(harness, project_dir, name=name, version=version)
