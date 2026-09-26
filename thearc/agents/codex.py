from thearc.agents.base import TargetAgentInstaller
from thearc.config import AGENT_CODEX


class CodexInstaller(TargetAgentInstaller):
    """Installer for OpenAI Codex / Copilot Agent."""

    agent_name = AGENT_CODEX
