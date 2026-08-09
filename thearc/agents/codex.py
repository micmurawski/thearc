from pathlib import Path
from typing import Dict, Any, Optional
from thearc.agents.base import BaseAgentInstaller
from thearc.config import get_global_agent_paths, get_local_agent_paths, AGENT_CODEX

class CodexInstaller(BaseAgentInstaller):
    """Installer for OpenAI Codex / Copilot Agent."""

    @property
    def name(self) -> str:
        return AGENT_CODEX

    def get_local_paths(self, project_dir: Path) -> Dict[str, Path]:
        return get_local_agent_paths(project_dir, AGENT_CODEX)

    def get_global_paths(self) -> Dict[str, Path]:
        return get_global_agent_paths(AGENT_CODEX)

    def format_skill_file(self, skill_name: str, raw_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format skill file for Codex."""
        if raw_content.startswith("<!-- Skill:") or raw_content.startswith("---"):
            return raw_content
        description = (metadata or {}).get("description", f"Skill for {skill_name}")
        header = f"<!-- Skill: {skill_name} | Description: {description} -->\n\n"
        return header + raw_content

    def format_command_file(self, command_name: str, raw_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Format prompt command file for Codex."""
        if raw_content.startswith("<!-- Prompt:") or raw_content.startswith("---"):
            return raw_content
        description = (metadata or {}).get("description", f"Prompt /{command_name}")
        header = f"<!-- Prompt: /{command_name} | {description} -->\n\n"
        return header + raw_content
