from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Optional
import shutil
import logging

logger = logging.getLogger("thearc")

class BaseAgentInstaller(ABC):
    """Abstract Base Class for Agent Plugin & Skill Installers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name identifier of the agent."""
        pass

    @abstractmethod
    def get_local_paths(self, project_dir: Path) -> Dict[str, Path]:
        """Return dict of local target directory paths for this agent."""
        pass

    @abstractmethod
    def get_global_paths(self) -> Dict[str, Path]:
        """Return dict of global target directory paths for this agent."""
        pass

    @abstractmethod
    def format_skill_file(self, skill_name: str, raw_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format skill markdown content specifically for this agent."""
        pass

    @abstractmethod
    def format_command_file(self, command_name: str, raw_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format slash command prompt content specifically for this agent."""
        pass

    def install_skill(
        self,
        skill_name: str,
        source_dir: Path,
        target_scope: str = "project",
        project_dir: Path = Path("."),
        force: bool = False
    ) -> List[Path]:
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
            return dest_file
        return dest_file
