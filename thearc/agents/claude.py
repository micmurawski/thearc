from pathlib import Path
from typing import Dict, Any, Optional
from thearc.agents.base import BaseAgentInstaller
from thearc.config import get_global_agent_paths, get_local_agent_paths, AGENT_CLAUDE

class ClaudeInstaller(BaseAgentInstaller):
    """Installer for Claude Code / Claude Agent."""

    @property
    def name(self) -> str:
        return AGENT_CLAUDE

    def get_local_paths(self, project_dir: Path) -> Dict[str, Path]:
        return get_local_agent_paths(project_dir, AGENT_CLAUDE)

    def get_global_paths(self) -> Dict[str, Path]:
        return get_global_agent_paths(AGENT_CLAUDE)

    def format_skill_file(self, skill_name: str, raw_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format skill file for Claude."""
        if raw_content.startswith("# Skill:") or raw_content.startswith("---"):
            return raw_content
        description = (metadata or {}).get("description", f"Skill for {skill_name}")
        header = f"# Skill: {skill_name}\n> {description}\n\n"
        return header + raw_content

    def format_command_file(self, command_name: str, raw_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format slash command prompt for Claude."""
        if raw_content.startswith("# Command:") or raw_content.startswith("---"):
            return raw_content
        description = (metadata or {}).get("description", f"Slash command /{command_name}")
        header = f"# Command: /{command_name}\n> {description}\n\n"
        return header + raw_content
