from __future__ import annotations

import json
from collections.abc import Iterator
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from thearc.models.markdown import MarkdownDocument, MarkdownSection
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


class Operation(str, Enum):
    ADD = "ADD"
    EDIT = "EDIT"
    REMOVE = "REMOVE"


class ResourceTarget(BaseModel):
    """Serializable address within a MetaAgent.

    ``name`` is a skill/hook name or context filename. For ``skill_file`` it
    is ``<skill>/<relative-file-path>``. ``section`` selects a Markdown heading.
    """

    model_config = {"frozen": True, "extra": "forbid"}
    kind: Literal["skill", "skill_file", "context", "hook", "mcp", "rule", "workflow", "command"]
    name: str = Field(min_length=1)
    section: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_address(self) -> ResourceTarget:
        if not self.name.strip() or (self.section is not None and not self.section.strip()):
            raise ValueError("resource name and section cannot be blank")
        if self.kind in {"hook", "mcp"} and self.section is not None:
            raise ValueError("hooks and MCPs do not have Markdown sections")
        if self.kind in {"skill_file", "context", "rule", "workflow", "command"}:
            path = PurePath(self.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("file targets must stay inside their resource directory")
        if self.kind == "skill_file":
            skill, separator, path = self.name.partition("/")
            if not separator or not skill or not path or ".." in PurePath(self.name).parts:
                raise ValueError("skill file target must be '<skill>/<relative-file-path>'")
        return self


class MetaAgent(BaseModel):
    """
    MetaAgent representation object consisting of attributes:
    skills, hooks, mcps, context (e.g. AGENTS.md, CLAUDE.md), each represented as objects.

    Provides install(implementation: Literal['claudecode', 'codex', 'anitgravity', 'pi'], replace=True, **kwargs)
    method to install the agent definition into specific agent harness directory targets.
    """
    model_config = {"extra": "forbid", "validate_assignment": True}
    name: str
    version: str | None = None
    skills: dict[str, Skill] = Field(default_factory=dict)
    hooks: dict[str, Hook] = Field(default_factory=dict)
    mcps: dict[str, MCP] = Field(default_factory=dict)
    context: dict[str, ContextDocument] = Field(default_factory=dict)
    resources: dict[str, AgentResource] = Field(default_factory=dict)

    @field_validator("name", "version")
    @classmethod
    def validate_identity(cls, value: str | None, info: Any) -> str | None:
        if value is None and info.field_name == "version":
            return None
        if value is None or not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"{info.field_name} must be a non-blank, single-line label")
        if info.field_name == "name":
            from thearc.models.identity import version_filename

            version_filename(value.strip())
        return value.strip()

    @field_validator("skills", "hooks", "mcps", "context", "resources", mode="before")
    @classmethod
    def normalize_collection(cls, value: Any, info: Any) -> dict:
        """Accept named mappings or lists, storing only typed dictionaries."""
        field = info.field_name
        identity = "filename" if field == "context" else "name"
        if isinstance(value, list):
            items = [(None, item) for item in value]
        elif isinstance(value, dict):
            items = list(value.items())
        else:
            raise ValueError(f"{field} must be a mapping or list")  # noqa: TRY004 -- Pydantic validation error
        normalized = {}
        for key, item in items:
            data = item.model_dump() if isinstance(item, BaseModel) else item
            if field == "context" and isinstance(data, str) and key is not None:
                data = {"filename": key, "content": data}
            if not isinstance(data, dict):
                raise ValueError(f"invalid {field} entry: {key}")  # noqa: TRY004 -- Pydantic validation error
            data = dict(data)
            if field == "resources":
                if "location" not in data or "path" not in data:
                    raise ValueError("resource entries require location and path")
                name = f'{data["location"]}/{data["path"]}'
            else:
                name = data.get(identity, key)
                if name is None:
                    raise ValueError(f"{field} entries require {identity}")
                data[identity] = name
            if key is not None and key != name:
                raise ValueError(f"{field} key {key!r} does not match identity {name!r}")
            if name in normalized:
                raise ValueError(f"duplicate {field} entry: {name}")
            normalized[name] = data
        return normalized

    @staticmethod
    def _add_ranks(before: Ranks | None, delta: Ranks) -> Ranks:
        """Return the accumulated rank counters without persisting anything."""
        before = before or Ranks()
        return Ranks(
            harmful=before.harmful + delta.harmful,
            neutral=before.neutral + delta.neutral,
            helpful=before.helpful + delta.helpful,
        )

    def iter_skill_files(self, *, markdown_only: bool = False) -> Iterator[tuple[str, str]]:
        """Yield bundled skill files using stable ``skill-name/path`` keys."""
        for skill in self.skills.values():
            for relative_path, content in skill.files.items():
                if not markdown_only or relative_path.endswith(".md"):
                    yield f"{skill.name}/{relative_path}", content

    def _target_collection(self, target: ResourceTarget) -> tuple[dict, str]:
        if target.kind == "skill_file":
            skill_name, _, path = target.name.partition("/")
            return self.skills[skill_name].files, path
        if target.kind in {"rule", "workflow", "command"}:
            return self.resources, f"{target.kind}s/{target.name}"
        collections = {
            "skill": self.skills,
            "hook": self.hooks,
            "mcp": self.mcps,
            "context": self.context,
        }
        return collections[target.kind], target.name

    def _target_markdown(self, target: ResourceTarget) -> MarkdownDocument:
        collection, key = self._target_collection(target)
        item = collection[key]
        text = item.instructions if target.kind == "skill" else (
            item.content if target.kind in {"context", "rule", "workflow", "command"} else item
        )
        return MarkdownDocument.parse(text)

    @staticmethod
    def _section_matches(document: MarkdownDocument, title: str) -> list[tuple[list, int]]:
        matches = []

        def visit(sections):
            for index, section in enumerate(sections):
                if section.title.strip().casefold() == title.strip().casefold():
                    matches.append((sections, index))
                visit(section.subsections)

        visit(document.sections)
        return matches

    def read_target(self, target: ResourceTarget) -> dict[str, Any] | str:
        """Resolve a resource address to a detached, JSON-serializable value."""
        if target.section is not None:
            matches = self._section_matches(self._target_markdown(target), target.section)
            if len(matches) != 1:
                raise KeyError(f"expected one Markdown section at {target}, found {len(matches)}")
            sections, index = matches[0]
            return sections[index].model_dump()
        collection, key = self._target_collection(target)
        item = collection[key]
        return item.model_dump() if isinstance(item, BaseModel) else item

    def apply_change(
        self, target: ResourceTarget, operation: Operation, value: dict[str, Any] | str | None = None,
    ) -> tuple[dict[str, Any] | str | None, dict[str, Any] | str | None]:
        """Apply ADD/EDIT/REMOVE in memory and return the before/after values.

        ADD requires an absent target; EDIT and REMOVE require an existing one.
        EDIT accepts a field patch for model resources and replacement text for
        bundled files. Section addresses must identify a unique heading.
        """
        operation = Operation(operation)
        if operation == Operation.REMOVE:
            if value is not None:
                raise ValueError("REMOVE does not accept a value")
        elif value is None:
            raise ValueError("ADD and EDIT require a value")
        collection, key = self._target_collection(target)
        if target.section is not None:
            document = self._target_markdown(target)
            matches = self._section_matches(document, target.section)
            if len(matches) > 1:
                raise ValueError(f"ambiguous Markdown section: {target.section}")
            exists = bool(matches)
            before = matches[0][0][matches[0][1]].model_dump() if exists else None
        else:
            exists = key in collection
            before = self.read_target(target) if exists else None
        if operation == Operation.ADD and exists:
            raise ValueError(f"target already exists: {target}")
        if operation != Operation.ADD and not exists:
            raise KeyError(f"target does not exist: {target}")

        if target.section is not None:
            if operation == Operation.REMOVE:
                sections, index = matches[0]
                sections.pop(index)
            else:
                if not isinstance(value, dict):
                    raise ValueError("section changes require a MarkdownSection field mapping")
                data = {**(before or {}), **value}
                if data.get("title", target.section) != target.section:
                    raise ValueError("section title must match its target")
                section = MarkdownSection.model_validate({**data, "title": target.section})
                if exists:
                    sections, index = matches[0]
                    sections[index] = section
                else:
                    document.sections.append(section)
            text = document.to_markdown()
            if target.kind == "skill":
                collection[key].instructions = text
            elif target.kind in {"context", "rule", "workflow", "command"}:
                collection[key].content = text
            else:
                collection[key] = text
        elif operation == Operation.REMOVE:
            del collection[key]
        elif target.kind == "skill_file":
            if not isinstance(value, str):
                raise ValueError("skill file changes require text")
            collection[key] = value
        else:
            if not isinstance(value, dict):
                raise ValueError("resource changes require a field mapping")
            model = {
                "skill": Skill, "hook": Hook, "context": ContextDocument, "mcp": MCP,
                "rule": AgentResource, "workflow": AgentResource, "command": AgentResource,
            }[target.kind]
            identities = (
                {"location": f"{target.kind}s", "path": target.name}
                if target.kind in {"rule", "workflow", "command"} else
                {"filename" if target.kind == "context" else "name": key}
            )
            data = {**(before or {}), **value}
            if any(data.get(field, expected) != expected for field, expected in identities.items()):
                raise ValueError("resource identity must match its target")
            collection[key] = model.model_validate({**data, **identities})
        return before, None if operation == Operation.REMOVE else self.read_target(target)

    def rank_for(self, target: ResourceTarget) -> Ranks:
        """Read the rank of an existing resource or unique Markdown section."""
        if target.kind == "mcp":
            raise ValueError("MCP configurations do not carry ranks")
        value = self.read_target(target)
        if target.section is None and target.kind in {"skill_file", "context", "rule", "workflow", "command"}:
            text = value if isinstance(value, str) else value["content"]
            ranks = MarkdownDocument.parse(text).frontmatter.get("ranks")
        else:
            ranks = value.get("ranks")
        return Ranks.model_validate(ranks) if ranks else Ranks()

    def apply_rank_delta(
        self, target: ResourceTarget, delta: Ranks,
    ) -> tuple[Any, Any]:
        """Increment ranks through the same addressed EDIT path as curations."""
        ranks = self._add_ranks(self.rank_for(target), delta)
        value = self.read_target(target)
        if target.section is None and target.kind in {"skill_file", "context", "rule", "workflow", "command"}:
            text = value if isinstance(value, str) else value["content"]
            document = MarkdownDocument.parse(text)
            document.frontmatter["ranks"] = ranks.model_dump()
            value = document.to_markdown() if target.kind == "skill_file" else {"content": document.to_markdown()}
        else:
            value = {"ranks": ranks.model_dump()}
        return self.apply_change(target, Operation.EDIT, value)

    def without_ranks(self) -> MetaAgent:
        """Return a deep-copied view suitable for an unbiased reflector."""
        view = self.model_copy(deep=True)
        for skill in view.skills.values():
            skill.ranks = None
            skill.metadata.pop("ranks", None)
            skill.instructions = MarkdownDocument.parse(skill.instructions).to_markdown(include_ranks=False)
            for relative_path, content in skill.files.items():
                if relative_path.endswith(".md"):
                    skill.files[relative_path] = MarkdownDocument.parse(content).to_markdown(include_ranks=False)
        for hook in view.hooks.values():
            hook.ranks = None
        for document in view.context.values():
            document.content = MarkdownDocument.parse(document.content).to_markdown(include_ranks=False)
        for resource in view.resources.values():
            resource.content = MarkdownDocument.parse(resource.content).to_markdown(include_ranks=False)
        return view

    def diff(
        self, other: MetaAgent, *, include_ranks: bool = False,
        include_version: bool = True, context_lines: int = 3,
    ) -> str:
        """Return a unified diff from this agent to ``other``; empty means no changes.

        Uses canonical workspace paths without writing files or mutating either
        agent. Rank annotations are hidden by default using ``without_ranks``
        (which normalizes Markdown). Set ``include_ranks=True`` for exact text.
        Version metadata is included unless explicitly disabled. Output is local
        configuration, not redacted: review it before sharing.
        """
        from thearc.models.diff import diff_agents

        return diff_agents(self, other, include_ranks=include_ranks,
                           include_version=include_version, context_lines=context_lines)

    @classmethod
    def from_workspace(cls, path: str | Path, *, max_bytes: int = 10_000_000, max_files: int = 10_000) -> MetaAgent:
        """Import a canonical editable workspace, without installing or executing it."""
        from thearc.models.workspace import read_workspace

        return read_workspace(path, max_bytes=max_bytes, max_files=max_files)

    def to_workspace(self, path: str | Path) -> Path:
        """Export canonical editable files to a new directory, without installation."""
        from thearc.models.workspace import write_workspace

        return write_workspace(self, path)

    @classmethod
    def from_project(
        cls,
        implementation: ImplementationLiteral,
        project_dir: str | Path = Path("."),
        name: str | None = None,
        version: str | None = None,
    ) -> MetaAgent:
        """Import an installed local agent configuration into the canonical model.

        This imports skills, canonical and Codex-native hooks, MCP servers,
        project instruction documents, and text artifacts in ``rules``,
        ``workflows``, and ``commands``. The returned configuration can be
        installed into a different implementation with :meth:`install`.
        """
        from thearc.agents import get_installer
        from thearc.models.identity import read_identity

        project_path = Path(project_dir).resolve()
        installer = get_installer(implementation)
        paths = installer.get_local_paths(project_path)
        identity = read_identity(paths["base"], name=name)
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
            name=name if name is not None else identity.get("name", f"{installer.name}-project"),
            version=version if version is not None else identity.get("version"),
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
        from thearc.models.identity import identity_path, read_identity, version_path, write_identity

        installer = get_installer(implementation)
        target_scope = kwargs.get("target_scope", "project")

        if path is not None:
            target_path = Path(path).resolve()
        elif "project_dir" in kwargs and kwargs["project_dir"] is not None:
            target_path = Path(kwargs["project_dir"]).resolve()
        else:
            target_path = Path.cwd().resolve()

        if target_scope not in {"project", "global"}:
            raise ValueError("target_scope must be 'project' or 'global'")
        paths = installer.get_local_paths(target_path) if target_scope == "project" else installer.get_global_paths()
        metadata_path = identity_path(paths["base"])
        version_path(paths["base"], self.name)
        if metadata_path.exists():
            read_identity(paths["base"])

        results: dict[str, list[Path]] = {
            "skills": [],
            "hooks": [],
            "mcps": [],
            "context": [],
            "resources": [],
        }

        # 1. Install Skills
        for skill in self.skills.values():
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
        for doc in self.context.values():
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
                resources=self.resources.values(),
                target_scope=target_scope,
                project_dir=target_path,
                force=replace,
            ))

        # Persist only the release label, not configuration or credentials.
        # Merge installs do not describe an exact release of this MetaAgent.
        results["metadata"] = write_identity(metadata_path, self.name, self.version if replace else None)

        return results


def _snake_case(value: str) -> str:
    """Convert native event names such as ``PreToolUse`` to ``pre_tool_use``."""
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
