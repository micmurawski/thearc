from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path, PurePath
from typing import Any, Literal

from pydantic import BaseModel, Field

from thearc.models.markdown import MarkdownDocument
from thearc.models.ranks import Ranks

ImplementationLiteral = Literal['claudecode', 'codex', 'anitgravity', 'antigravity', 'claude', 'pi']


class Skill(BaseModel):
    """Represents an agent skill object."""
    ranks: Ranks | None = None
    name: str
    description: str = ""
    instructions: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_markdown(cls, name: str, markdown_text: str, description: str = "") -> Skill:
        doc = MarkdownDocument.parse(markdown_text)
        meta = doc.frontmatter or {}
        desc = meta.get("description", description)
        instr = doc.root_content
        if doc.sections:
            sec_md = "\n\n".join([s.to_markdown() for s in doc.sections])
            instr = f"{instr}\n\n{sec_md}".strip()
        return cls(name=name, description=desc, instructions=instr,
                   ranks=meta.get("ranks"), metadata={k: v for k, v in meta.items() if k != "ranks"})

    @classmethod
    def from_dir(cls, dir_path: Path) -> Skill:
        dir_path = Path(dir_path)
        skill_file = dir_path / "SKILL.md"
        if not skill_file.exists():
            raise FileNotFoundError(f"No SKILL.md found in {dir_path}")
        skill = cls.from_markdown(name=dir_path.name, markdown_text=skill_file.read_text(encoding="utf-8"))
        for item in dir_path.rglob("*"):
            if item.is_file() and item.name != "SKILL.md":
                rel_path = str(item.relative_to(dir_path))
                skill.files[rel_path] = item.read_text(encoding="utf-8")
        return skill

    def to_markdown(self, include_ranks: bool = True) -> str:
        doc = MarkdownDocument.parse(self.instructions)
        doc.frontmatter = {**self.metadata, "name": self.name, "description": self.description}
        doc.frontmatter.pop("ranks", None)
        if include_ranks and self.ranks is not None:
            doc.frontmatter["ranks"] = self.ranks.model_dump()
        return doc.to_markdown(include_ranks=include_ranks)

    def write(self, path: str | Path, include_ranks: bool = True) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_markdown(include_ranks=include_ranks), encoding="utf-8")


class Hook(BaseModel):
    """Represents an agent hook object."""
    ranks: Ranks | None = None
    name: str
    event: str = "pre_tool_use"
    command: str | None = None
    script: str | None = None
    matcher: str | None = None
    description: str = ""
    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self, include_ranks: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "event": self.event,
            "enabled": self.enabled,
        }
        if self.command:
            data["command"] = self.command
        if self.script:
            data["script"] = self.script
        if self.matcher:
            data["matcher"] = self.matcher
        if self.description:
            data["description"] = self.description
        if self.extra:
            data.update(self.extra)
        data.pop("ranks", None)
        if include_ranks and self.ranks is not None:
            data["ranks"] = self.ranks.model_dump()
        return data


class MCP(BaseModel):
    """Represents an MCP (Model Context Protocol) server configuration object."""
    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    transport: str = "stdio"
    disabled: bool = False
    auto_approve: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.url:
            data["url"] = self.url
            data["transport"] = self.transport
        if self.command:
            data["command"] = self.command
        if self.args:
            data["args"] = self.args
        if self.env:
            data["env"] = self.env
        if self.disabled:
            data["disabled"] = self.disabled
        if self.auto_approve:
            data["autoApprove"] = self.auto_approve
        if self.extra:
            data.update(self.extra)
        return data


class ContextDocument(BaseModel):
    """Represents a context document object (e.g. AGENTS.md, CLAUDE.md, GEMINI.md, etc.)."""
    filename: str
    content: str
    description: str = ""

    @classmethod
    def from_file(cls, path: Path) -> ContextDocument:
        p = Path(path)
        return cls(filename=p.name, content=p.read_text(encoding="utf-8"))

    @classmethod
    def from_markdown(cls, filename: str, content: str, description: str = "") -> ContextDocument:
        return cls(filename=filename, content=content, description=description)

    def to_markdown(self, include_ranks: bool = True) -> str:
        return MarkdownDocument.parse(self.content).to_markdown(include_ranks=include_ranks)

    def write(self, path: str | Path, include_ranks: bool = True) -> None:
        MarkdownDocument.parse(self.content).write(path, include_ranks=include_ranks)


class AgentResource(BaseModel):
    """An agent-local text artifact such as a rule, workflow, or command."""

    location: Literal["rules", "workflows", "commands"]
    path: str
    content: str

    def relative_path(self) -> Path:
        """Return a safe path for installation below the resource location."""
        candidate = PurePath(self.path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"Resource path must be relative and cannot escape its target: {self.path}")
        return Path(candidate)


class AgentResourceSet(BaseModel):
    """Container for agent-local text artifacts."""

    resources: dict[str, AgentResource] = Field(default_factory=dict)

    def add(self, resource: AgentResource | dict[str, Any]) -> AgentResourceSet:
        if isinstance(resource, dict):
            resource = AgentResource(**resource)
        self.resources[f"{resource.location}/{resource.path}"] = resource
        return self

    def __iter__(self) -> Iterator[AgentResource]:
        return iter(self.resources.values())

    def __len__(self) -> int:
        return len(self.resources)

    def __bool__(self) -> bool:
        return bool(self.resources)


class SkillSet(BaseModel):
    """Container object for skills."""
    skills: dict[str, Skill] = Field(default_factory=dict)

    def add(self, skill: Skill | dict[str, Any]) -> SkillSet:
        if isinstance(skill, dict):
            skill = Skill(**skill)
        self.skills[skill.name] = skill
        return self

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def remove(self, name: str) -> None:
        self.skills.pop(name, None)

    def __getitem__(self, name: str) -> Skill:
        return self.skills[name]

    def __iter__(self) -> Iterator[Skill]:
        return iter(self.skills.values())

    def __len__(self) -> int:
        return len(self.skills)

    def __bool__(self) -> bool:
        return bool(self.skills)


class HookSet(BaseModel):
    """Container object for hooks."""
    hooks: dict[str, Hook] = Field(default_factory=dict)

    def add(self, hook: Hook | dict[str, Any]) -> HookSet:
        if isinstance(hook, dict):
            hook = Hook(**hook)
        self.hooks[hook.name] = hook
        return self

    def get(self, name: str) -> Hook | None:
        return self.hooks.get(name)

    def remove(self, name: str) -> None:
        self.hooks.pop(name, None)

    def to_dict(self, include_ranks: bool = True) -> dict[str, Any]:
        return {name: hook.to_dict(include_ranks=include_ranks) for name, hook in self.hooks.items()}

    def __getitem__(self, name: str) -> Hook:
        return self.hooks[name]

    def __iter__(self) -> Iterator[Hook]:
        return iter(self.hooks.values())

    def __len__(self) -> int:
        return len(self.hooks)

    def __bool__(self) -> bool:
        return bool(self.hooks)


class MCPSet(BaseModel):
    """Container object for MCP server configurations."""
    mcps: dict[str, MCP] = Field(default_factory=dict)

    def add(self, mcp: MCP | dict[str, Any]) -> MCPSet:
        if isinstance(mcp, dict):
            mcp = MCP(**mcp)
        self.mcps[mcp.name] = mcp
        return self

    def get(self, name: str) -> MCP | None:
        return self.mcps.get(name)

    def remove(self, name: str) -> None:
        self.mcps.pop(name, None)

    def to_dict(self) -> dict[str, Any]:
        return {name: mcp.to_dict() for name, mcp in self.mcps.items()}

    def __getitem__(self, name: str) -> MCP:
        return self.mcps[name]

    def __iter__(self) -> Iterator[MCP]:
        return iter(self.mcps.values())

    def __len__(self) -> int:
        return len(self.mcps)

    def __bool__(self) -> bool:
        return bool(self.mcps)


class ContextSet(BaseModel):
    """Container object for context documents (AGENTS.md, CLAUDE.md, etc.)."""
    documents: dict[str, ContextDocument] = Field(default_factory=dict)

    def add(self, doc: ContextDocument | dict[str, Any]) -> ContextSet:
        if isinstance(doc, dict):
            doc = ContextDocument(**doc)
        self.documents[doc.filename] = doc
        return self

    def get(self, filename: str) -> ContextDocument | None:
        return self.documents.get(filename)

    def remove(self, filename: str) -> None:
        self.documents.pop(filename, None)

    @property
    def agents_md(self) -> ContextDocument | None:
        return self.get("AGENTS.md")

    @property
    def claude_md(self) -> ContextDocument | None:
        return self.get("CLAUDE.md")

    def __getitem__(self, filename: str) -> ContextDocument:
        return self.documents[filename]

    def __iter__(self) -> Iterator[ContextDocument]:
        return iter(self.documents.values())

    def __len__(self) -> int:
        return len(self.documents)

    def __bool__(self) -> bool:
        return bool(self.documents)


class Agent(BaseModel):
    """
    Agent representation object consisting of attributes:
    skills, hooks, mcps, context (e.g. AGENTS.md, CLAUDE.md), each represented as objects.

    Provides install(implementation: Literal['claudecode', 'codex', 'anitgravity', 'pi'], replace=True, **kwargs)
    method to install the agent definition into specific agent harness directory targets.
    """
    name: str = "agent"
    skills: SkillSet = Field(default_factory=SkillSet)
    hooks: HookSet = Field(default_factory=HookSet)
    mcps: MCPSet = Field(default_factory=MCPSet)
    context: ContextSet = Field(default_factory=ContextSet)
    resources: AgentResourceSet = Field(default_factory=AgentResourceSet)

    @property
    def conetxt(self) -> ContextSet:
        """Alias for context attribute to support user typo compatibility."""
        return self.context

    @conetxt.setter
    def conetxt(self, value: Any) -> None:
        self.context = self._parse_context(value)

    def __init__(
        self,
        name: str = "agent",
        skills: SkillSet | list[Skill | dict[str, Any]] | dict[str, Skill | dict[str, Any]] | None = None,
        hooks: HookSet | list[Hook | dict[str, Any]] | dict[str, Hook | dict[str, Any]] | None = None,
        mcps: MCPSet | list[MCP | dict[str, Any]] | dict[str, MCP | dict[str, Any]] | None = None,
        context: ContextSet | list[ContextDocument | dict[str, Any]] | dict[str, str] | None = None,
        resources: AgentResourceSet | list[AgentResource | dict[str, Any]] | None = None,
        conetxt: ContextSet | list[ContextDocument | dict[str, Any]] | dict[str, str] | None = None,
        **data
    ):
        super().__init__(name=name, **data)

        if skills is not None:
            self.skills = self._parse_skills(skills)

        if hooks is not None:
            self.hooks = self._parse_hooks(hooks)

        if mcps is not None:
            self.mcps = self._parse_mcps(mcps)

        ctx_value = context if context is not None else conetxt
        if ctx_value is not None:
            self.context = self._parse_context(ctx_value)

        if resources is not None:
            self.resources = self._parse_resources(resources)

    def _parse_skills(self, value: Any) -> SkillSet:
        if isinstance(value, SkillSet):
            return value
        s_set = SkillSet()
        if isinstance(value, list):
            for item in value:
                s_set.add(item)
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, Skill):
                    s_set.add(v)
                elif isinstance(v, dict):
                    if "name" not in v:
                        v["name"] = k
                    s_set.add(v)
        return s_set

    def _parse_hooks(self, value: Any) -> HookSet:
        if isinstance(value, HookSet):
            return value
        h_set = HookSet()
        if isinstance(value, list):
            for item in value:
                h_set.add(item)
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, Hook):
                    h_set.add(v)
                elif isinstance(v, dict):
                    if "name" not in v:
                        v["name"] = k
                    h_set.add(v)
        return h_set

    def _parse_mcps(self, value: Any) -> MCPSet:
        if isinstance(value, MCPSet):
            return value
        m_set = MCPSet()
        if isinstance(value, list):
            for item in value:
                m_set.add(item)
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, MCP):
                    m_set.add(v)
                elif isinstance(v, dict):
                    if "name" not in v:
                        v["name"] = k
                    m_set.add(v)
        return m_set

    def _parse_context(self, value: Any) -> ContextSet:
        if isinstance(value, ContextSet):
            return value
        c_set = ContextSet()
        if isinstance(value, list):
            for item in value:
                c_set.add(item)
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, ContextDocument):
                    c_set.add(v)
                elif isinstance(v, str):
                    c_set.add(ContextDocument(filename=k, content=v))
                elif isinstance(v, dict):
                    if "filename" not in v:
                        v["filename"] = k
                    c_set.add(v)
        return c_set

    def _parse_resources(self, value: Any) -> AgentResourceSet:
        if isinstance(value, AgentResourceSet):
            return value
        resource_set = AgentResourceSet()
        if isinstance(value, list):
            for item in value:
                resource_set.add(item)
        return resource_set

    @classmethod
    def from_project(
        cls,
        implementation: ImplementationLiteral,
        project_dir: str | Path = Path("."),
        name: str | None = None,
    ) -> Agent:
        """Import an installed local agent configuration into the canonical model.

        This imports skills, canonical and Codex-native hooks, MCP servers,
        project instruction documents, and text artifacts in ``rules``,
        ``workflows``, and ``commands``. The returned configuration can be
        installed into a different implementation with :meth:`install`.
        """
        from thearc.agents import get_installer

        project_path = Path(project_dir).resolve()
        installer = get_installer(implementation)
        paths = installer.get_local_paths(project_path)
        skills_dir = paths["skills"]
        skills = (
            [
                Skill.from_dir(skill_dir)
                for skill_dir in sorted(skills_dir.iterdir())
                if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file()
            ]
            if skills_dir.is_dir()
            else []
        )
        context = [
            ContextDocument.from_file(project_path / filename)
            for filename in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "PI.md")
            if (project_path / filename).is_file()
        ]
        return cls(
            name=name or f"{installer.name}-project",
            skills=skills,
            hooks=cls._read_hooks(paths["base"] / "hooks.json"),
            mcps=cls._read_mcps(paths["mcp"]),
            context=context,
            resources=cls._read_resources(paths),
        )

    @staticmethod
    def _read_hooks(hooks_file: Path) -> list[Hook]:
        if not hooks_file.is_file():
            return []
        try:
            raw_hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []

        # Codex uses {"hooks": {"PreToolUse": [{"matcher": ..., "hooks": [...]}]}}.
        if isinstance(raw_hooks.get("hooks"), dict):
            imported: list[Hook] = []
            for event, groups in raw_hooks["hooks"].items():
                for group_index, group in enumerate(groups):
                    for hook_index, definition in enumerate(group.get("hooks", [])):
                        command = definition.get("command")
                        if not command:
                            continue
                        imported.append(Hook(
                            name=f"{event}-{group_index}-{hook_index}",
                            event=_snake_case(event),
                            matcher=group.get("matcher"),
                            command=command,
                            extra={key: value for key, value in definition.items() if key != "command"},
                        ))
            return imported

        if not isinstance(raw_hooks, dict):
            return []
        return [
            Hook(name=hook_name, **{key: value for key, value in definition.items() if key != "name"})
            for hook_name, definition in raw_hooks.items()
            if isinstance(definition, dict)
        ]

    @staticmethod
    def _read_mcps(mcp_file: Path) -> list[MCP]:
        if not mcp_file.is_file():
            return []
        try:
            servers = json.loads(mcp_file.read_text(encoding="utf-8")).get("mcpServers", {})
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        return [MCP(name=server_name, **definition) for server_name, definition in servers.items()]

    @staticmethod
    def _read_resources(paths: dict[str, Path]) -> list[AgentResource]:
        locations = {
            "rules": paths["rules"],
            "commands": paths["commands"],
            "workflows": paths["base"] / "workflows",
        }
        resources: list[AgentResource] = []
        for location, directory in locations.items():
            if not directory.is_dir():
                continue
            for source_file in sorted(path for path in directory.rglob("*") if path.is_file()):
                try:
                    content = source_file.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                resources.append(AgentResource(
                    location=location,
                    path=str(source_file.relative_to(directory)),
                    content=content,
                ))
        return resources

    def install(
        self,
        implementation: ImplementationLiteral,
        replace: bool = True,
        path: str | Path | None = None,
        **kwargs
    ) -> dict[str, list[Path]]:
        """
        Install the agent's skills, hooks, mcps, and context attributes into target implementation.

        :param implementation: Target implementation ('claudecode', 'codex', 'anitgravity', 'pi')
        :param replace: Whether to overwrite existing target files (default: True)
        :param path: Installation target directory path (overrides project_dir if provided)
        :param kwargs: Additional installer options such as target_scope='project'|'global', project_dir=Path('.')
        :return: Dict mapping component names to list of installed file Paths
        """
        from thearc.agents import get_installer

        installer = get_installer(implementation)
        target_scope = kwargs.get("target_scope", "project")

        if path is not None:
            target_path = Path(path).resolve()
        elif "project_dir" in kwargs and kwargs["project_dir"] is not None:
            target_path = Path(kwargs["project_dir"]).resolve()
        else:
            target_path = Path.cwd().resolve()

        results: dict[str, list[Path]] = {
            "skills": [],
            "hooks": [],
            "mcps": [],
            "context": [],
            "resources": [],
        }

        # 1. Install Skills
        for skill in self.skills:
            files = installer.install_skill_object(
                skill=skill,
                target_scope=target_scope,
                project_dir=target_path,
                force=replace
            )
            results["skills"].extend(files)

        # 2. Install Hooks
        if self.hooks:
            hook_files = installer.install_hooks(
                hooks=self.hooks,
                target_scope=target_scope,
                project_dir=target_path,
                force=replace
            )
            results["hooks"].extend(hook_files)

        # 3. Install MCPs
        if self.mcps:
            mcp_files = installer.install_mcps(
                mcps=self.mcps,
                target_scope=target_scope,
                project_dir=target_path,
                force=replace
            )
            results["mcps"].extend(mcp_files)

        # 4. Install Context Documents (AGENTS.md, CLAUDE.md, etc.)
        for doc in self.context:
            doc_files = installer.install_context_doc(
                doc=doc,
                target_scope=target_scope,
                project_dir=target_path,
                force=replace
            )
            results["context"].extend(doc_files)

        # 5. Install portable agent-local files such as rules and workflows.
        if self.resources:
            results["resources"].extend(installer.install_resources(
                resources=self.resources,
                target_scope=target_scope,
                project_dir=target_path,
                force=replace,
            ))

        return results


def _snake_case(value: str) -> str:
    """Convert native event names such as ``PreToolUse`` to ``pre_tool_use``."""
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
