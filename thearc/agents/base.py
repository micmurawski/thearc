from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from thearc.config import get_global_agent_paths, get_local_agent_paths
from thearc.models.schemas import CommandDefinition, SkillDefinition
from thearc.models.strategies import get_strategy

logger = logging.getLogger("thearc")

def merge_markdown_documents(existing_text: str, new_text: str) -> str:
    """
    Merge new markdown content into existing markdown content when replace=False.
    Merges frontmatter, updates matching sections, and appends non-matching sections.
    """
    from thearc.models.markdown import MarkdownDocument, MarkdownSection

    if not existing_text.strip():
        return new_text

    existing_doc = MarkdownDocument.parse(existing_text)
    new_doc = MarkdownDocument.parse(new_text)

    if new_doc.frontmatter:
        existing_doc.frontmatter.update(new_doc.frontmatter)

    if not existing_doc.root_content.strip() and new_doc.root_content.strip():
        existing_doc.root_content = new_doc.root_content.strip()
    elif new_doc.root_content.strip() and new_doc.root_content.strip() not in existing_doc.root_content:
        existing_doc.root_content = f"{existing_doc.root_content}\n\n{new_doc.root_content}".strip()

    def _merge_sections(existing_sections: list[MarkdownSection], new_sections: list[MarkdownSection]):
        for new_sec in new_sections:
            matched = False
            for ext_sec in existing_sections:
                if ext_sec.title.strip().lower() == new_sec.title.strip().lower() or ext_sec.slug == new_sec.slug:
                    if new_sec.content:
                        ext_sec.content = new_sec.content
                    if new_sec.subsections:
                        _merge_sections(ext_sec.subsections, new_sec.subsections)
                    matched = True
                    break
            if not matched:
                existing_sections.append(new_sec)

    _merge_sections(existing_doc.sections, new_doc.sections)
    return existing_doc.to_markdown()


class BaseAgentInstaller(ABC):
    """Abstract Base Class for Agent Plugin & Skill Installers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name identifier of the agent."""

    @abstractmethod
    def get_local_paths(self, project_dir: Path) -> dict[str, Path]:
        """Return dict of local target directory paths for this agent."""

    @abstractmethod
    def get_global_paths(self) -> dict[str, Path]:
        """Return dict of global target directory paths for this agent."""

    @abstractmethod
    def format_skill_file(self, skill_name: str, raw_content: str, metadata: dict[str, Any] | None = None) -> str:
        """Format skill markdown content specifically for this agent."""

    @abstractmethod
    def format_command_file(self, command_name: str, raw_content: str, metadata: dict[str, Any] | None = None) -> str:
        """Format slash command prompt content specifically for this agent."""

    def install_skill(
        self,
        skill_name: str,
        source_dir: Path,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False
    ) -> list[Path]:
        """Install a skill (directory or SKILL.md file) into the target agent location."""
        paths = self.get_local_paths(project_dir) if target_scope == "project" else self.get_global_paths()
        target_skills_dir = paths["skills"]
        installed_files = []

        target_skill_dir = target_skills_dir / skill_name
        target_skill_dir.mkdir(parents=True, exist_ok=True)

        if source_dir.is_dir():
            for item in source_dir.rglob("*"):
                if item.is_file():
                    rel_path = item.relative_to(source_dir)
                    dest_file = target_skill_dir / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)

                    if item.name == "SKILL.md":
                        content = item.read_text(encoding="utf-8")
                        formatted = self.format_skill_file(skill_name, content)
                        if not dest_file.exists() or force:
                            dest_file.write_text(formatted, encoding="utf-8")
                            installed_files.append(dest_file)
                        else:
                            existing_text = dest_file.read_text(encoding="utf-8")
                            merged_text = merge_markdown_documents(existing_text, formatted)
                            dest_file.write_text(merged_text, encoding="utf-8")
                            installed_files.append(dest_file)
                    else:
                        if not dest_file.exists() or force:
                            shutil.copy2(item, dest_file)
                            installed_files.append(dest_file)
        elif source_dir.is_file():
            dest_file = target_skill_dir / "SKILL.md"
            content = source_dir.read_text(encoding="utf-8")
            formatted = self.format_skill_file(skill_name, content)
            if not dest_file.exists() or force:
                dest_file.write_text(formatted, encoding="utf-8")
                installed_files.append(dest_file)
            else:
                existing_text = dest_file.read_text(encoding="utf-8")
                merged_text = merge_markdown_documents(existing_text, formatted)
                dest_file.write_text(merged_text, encoding="utf-8")
                installed_files.append(dest_file)

        return installed_files

    def install_command(
        self,
        command_name: str,
        source_file: Path,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False
    ) -> Path:
        """Install a slash command prompt definition into the target agent location."""
        paths = self.get_local_paths(project_dir) if target_scope == "project" else self.get_global_paths()
        target_commands_dir = paths["commands"]
        target_commands_dir.mkdir(parents=True, exist_ok=True)

        dest_file = target_commands_dir / f"{command_name}.md"
        content = source_file.read_text(encoding="utf-8") if source_file.is_file() else str(source_file)
        formatted = self.format_command_file(command_name, content)

        if not dest_file.exists() or force:
            dest_file.write_text(formatted, encoding="utf-8")
        else:
            existing_text = dest_file.read_text(encoding="utf-8")
            merged_text = merge_markdown_documents(existing_text, formatted)
            dest_file.write_text(merged_text, encoding="utf-8")
        return dest_file

    def ensure_config_file(
        self,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False
    ) -> Path:
        """Create default .thearc.toml configuration file in the agent target base directory."""
        paths = self.get_local_paths(project_dir) if target_scope == "project" else self.get_global_paths()
        base_dir = paths["base"]
        base_dir.mkdir(parents=True, exist_ok=True)
        config_file = base_dir / ".thearc.toml"

        if not config_file.exists() or force:
            content = (
                f"# thearc configuration file for {self.name} agent\n"
                f"version = \"0.1.0\"\n\n"
                f"[agent]\n"
                f"name = \"{self.name}\"\n\n"
                f"[cli]\n"
                f"default_scope = \"{target_scope}\"\n"
                f"default_agent = \"{self.name}\"\n"
            )
            config_file.write_text(content, encoding="utf-8")

        return config_file

    def install_skill_object(
        self,
        skill: Any,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False
    ) -> list[Path]:
        """
        Install a Skill model object into the target agent location.
        If force=True (replace=True), replaces the file completely.
        If force=False (replace=False), merges sections and updates content.
        """
        paths = self.get_local_paths(project_dir) if target_scope == "project" else self.get_global_paths()
        target_skills_dir = paths["skills"]
        target_skill_dir = target_skills_dir / skill.name
        target_skill_dir.mkdir(parents=True, exist_ok=True)

        installed_files = []
        dest_file = target_skill_dir / "SKILL.md"

        raw_content = skill.instructions if skill.instructions else f"# {skill.name}"
        meta = {"description": skill.description}
        if hasattr(skill, "metadata") and skill.metadata:
            meta.update(skill.metadata)

        if getattr(skill, "ranks", None) is not None:
            meta["ranks"] = skill.ranks

        formatted = self.format_skill_file(skill.name, raw_content, metadata=meta)

        if not dest_file.exists() or force:
            dest_file.write_text(formatted, encoding="utf-8")
            installed_files.append(dest_file)
        else:
            existing_text = dest_file.read_text(encoding="utf-8")
            merged_text = merge_markdown_documents(existing_text, formatted)
            dest_file.write_text(merged_text, encoding="utf-8")
            installed_files.append(dest_file)

        if hasattr(skill, "files") and skill.files:
            for rel_path_str, file_content in skill.files.items():
                sub_file = target_skill_dir / rel_path_str
                sub_file.parent.mkdir(parents=True, exist_ok=True)
                if not sub_file.exists() or force:
                    sub_file.write_text(file_content, encoding="utf-8")
                    installed_files.append(sub_file)
                else:
                    if rel_path_str.endswith(".md"):
                        ex_sub = sub_file.read_text(encoding="utf-8")
                        merged_sub = merge_markdown_documents(ex_sub, file_content)
                        sub_file.write_text(merged_sub, encoding="utf-8")
                        installed_files.append(sub_file)

        return installed_files

    def install_hooks(
        self,
        hooks: Any,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False
    ) -> list[Path]:
        """
        Install hooks into the target agent location.
        If force=True (replace=True), replaces existing hooks.json completely.
        If force=False (replace=False), appends/updates hook definitions in hooks.json.
        """
        if not hooks:
            return []
        paths = self.get_local_paths(project_dir) if target_scope == "project" else self.get_global_paths()
        hooks_dir = paths.get("hooks", paths["base"] / "hooks")
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hooks_json_file = paths["base"] / "hooks.json"

        import json
        if self.name == "codex":
            existing_hooks = {"hooks": {}}
            if not force and hooks_json_file.exists():
                try:
                    loaded = json.loads(hooks_json_file.read_text(encoding="utf-8"))
                    if isinstance(loaded.get("hooks"), dict):
                        existing_hooks = loaded
                except (OSError, UnicodeError, json.JSONDecodeError):
                    pass

            for hook in hooks.values():
                event = "".join(part.capitalize() for part in hook.event.split("_"))
                groups = existing_hooks["hooks"].setdefault(event, [])
                group = next((item for item in groups if item.get("matcher") == hook.matcher), None)
                if group is None:
                    group = {"matcher": hook.matcher, "hooks": []}
                    groups.append(group)
                definition = {"type": "command"}
                if hook.command:
                    definition["command"] = hook.command
                elif hook.script:
                    definition["command"] = hook.script
                if hook.extra:
                    definition.update(hook.extra)
                group["hooks"].append(definition)

            hooks_json_file.write_text(json.dumps(existing_hooks, indent=2), encoding="utf-8")
            return [hooks_json_file]

        existing_hooks = {}
        if hooks_json_file.exists():
            try:
                existing_hooks = json.loads(hooks_json_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing_hooks = {}

        hooks_dict = {name: hook.to_dict() for name, hook in hooks.items()}
        if force:
            merged = hooks_dict
        else:
            merged = {**existing_hooks, **hooks_dict}

        hooks_json_file.parent.mkdir(parents=True, exist_ok=True)
        hooks_json_file.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        installed_files = [hooks_json_file]

        for hook in hooks.values():
            if hasattr(hook, "script") and hook.script:
                script_file = hooks_dir / f"{hook.name}.sh"
                if not script_file.exists() or force:
                    script_file.write_text(hook.script, encoding="utf-8")
                    installed_files.append(script_file)

        return installed_files

    def install_mcps(
        self,
        mcps: Any,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False
    ) -> list[Path]:
        """
        Install MCP server configurations into the target agent location.
        If force=True (replace=True), replaces mcp.json/mcp_config.json completely.
        If force=False (replace=False), appends/updates server configs in mcp.json.
        """
        if not mcps:
            return []
        paths = self.get_local_paths(project_dir) if target_scope == "project" else self.get_global_paths()
        mcp_file = paths.get("mcp", paths["base"] / "mcp.json")
        mcp_file.parent.mkdir(parents=True, exist_ok=True)

        import json
        existing_data = {"mcpServers": {}}
        if mcp_file.exists():
            try:
                existing_data = json.loads(mcp_file.read_text(encoding="utf-8"))
                if "mcpServers" not in existing_data:
                    existing_data["mcpServers"] = {}
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing_data = {"mcpServers": {}}

        mcp_dict = {name: mcp.to_dict() for name, mcp in mcps.items()}
        if force:
            existing_data["mcpServers"] = mcp_dict
        else:
            existing_data["mcpServers"].update(mcp_dict)

        mcp_file.write_text(json.dumps(existing_data, indent=2), encoding="utf-8")
        return [mcp_file]

    def install_context_doc(
        self,
        doc: Any,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False
    ) -> list[Path]:
        """
        Install context document (AGENTS.md, CLAUDE.md, etc.) into project or agent directory.
        If force=True (replace=True), replaces the file completely.
        If force=False (replace=False), merges sections and updates content.
        """
        filename = doc.filename if hasattr(doc, "filename") else "AGENTS.md"
        content = doc.content if hasattr(doc, "content") else str(doc)

        if target_scope == "project":
            dest_file = Path(project_dir).resolve() / filename
        else:
            paths = self.get_global_paths()
            dest_file = paths["base"] / filename

        dest_file.parent.mkdir(parents=True, exist_ok=True)

        if force or not dest_file.exists():
            dest_file.write_text(content, encoding="utf-8")
            return [dest_file]
        existing_text = dest_file.read_text(encoding="utf-8")
        merged_text = merge_markdown_documents(existing_text, content)
        dest_file.write_text(merged_text, encoding="utf-8")
        return [dest_file]

    def install_resources(
        self,
        resources: Any,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False,
    ) -> list[Path]:
        """Install portable agent-local files such as rules and workflows."""
        paths = self.get_local_paths(project_dir) if target_scope == "project" else self.get_global_paths()
        locations = {
            "rules": paths["rules"],
            "commands": paths["commands"],
            "workflows": paths["base"] / "workflows",
        }
        installed = []
        for resource in resources:
            dest_file = locations[resource.location] / resource.relative_path()
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            if force or not dest_file.exists():
                dest_file.write_text(resource.content, encoding="utf-8")
            elif dest_file.suffix.lower() == ".md":
                existing_text = dest_file.read_text(encoding="utf-8")
                dest_file.write_text(merge_markdown_documents(existing_text, resource.content), encoding="utf-8")
            installed.append(dest_file)
        return installed


class TargetAgentInstaller(BaseAgentInstaller):
    """Shared installer implementation for a configured agent target."""

    agent_name: ClassVar[str]

    @property
    def name(self) -> str:
        return self.agent_name

    def get_local_paths(self, project_dir: Path) -> dict[str, Path]:
        return get_local_agent_paths(project_dir, self.agent_name)

    def get_global_paths(self) -> dict[str, Path]:
        return get_global_agent_paths(self.agent_name)

    def format_skill_file(
        self,
        skill_name: str,
        raw_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        skill = SkillDefinition.from_markdown(name=skill_name, markdown_text=raw_content)
        for key, value in (metadata or {}).items():
            setattr(skill.metadata, key, value)
        return get_strategy(self.agent_name).skill_to_markdown(skill)

    def format_command_file(
        self,
        command_name: str,
        raw_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        command = CommandDefinition.from_markdown(name=command_name, markdown_text=raw_content)
        if metadata and "description" in metadata:
            command.description = metadata["description"]
        return get_strategy(self.agent_name).command_to_markdown(command)
