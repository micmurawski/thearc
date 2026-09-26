from thearc.agents.base import TargetAgentInstaller
from thearc.config import AGENT_CLAUDE


class ClaudeInstaller(TargetAgentInstaller):
    """Installer for Claude Code / Claude Agent."""

    agent_name = AGENT_CLAUDE
