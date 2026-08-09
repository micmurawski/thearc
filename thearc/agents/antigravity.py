from pathlib import Path
from typing import Dict, Any, Optional
from thearc.agents.base import BaseAgentInstaller
from thearc.config import get_global_agent_paths, get_local_agent_paths, AGENT_ANTIGRAVITY

class AntigravityInstaller(BaseAgentInstaller):
    """Installer for Antigravity Agent."""

    @property
    def name(self) -> str:
        return AGENT_ANTIGRAVITY

    def get_local_paths(self, project_dir: Path) -> Dict[str, Path]:
        return get_local_agent_paths(project_dir, AGENT_ANTIGRAVITY)

    def get_global_paths(self) -> Dict[str, Path]:
        return get_global_agent_paths(AGENT_ANTIGRAVITY)

    def format_skill_file(self, skill_name: str, raw_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format skill file for Antigravity (YAML frontmatter + instructions)."""
        if raw_content.startswith("---"):
            return raw_content
        description = (metadata or {}).get("description", f"Skill for {skill_name}")
        frontmatter = f"---\nname: {skill_name}\ndescription: {description}\n---\n\n"
        return frontmatter + raw_content

    def format_command_file(self, command_name: str, raw_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format command file for Antigravity slash commands."""
        if raw_content.startswith("---"):
            return raw_content
        description = (metadata or {}).get("description", f"Slash command /{command_name}")
        header = f"---\ndescription: {description}\n---\n\n"
        return header + raw_content
