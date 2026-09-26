from thearc.agents.base import TargetAgentInstaller
from thearc.config import AGENT_PI


class PiInstaller(TargetAgentInstaller):
    """Installer for Pi Agent."""

    agent_name = AGENT_PI
