from typing import Dict, List, Type
from thearc.agents.base import BaseAgentInstaller
from thearc.agents.antigravity import AntigravityInstaller
from thearc.agents.claude import ClaudeInstaller
from thearc.agents.codex import CodexInstaller
from thearc.config import SUPPORTED_AGENTS

_INSTALLER_REGISTRY: Dict[str, Type[BaseAgentInstaller]] = {
    "antigravity": AntigravityInstaller,
    "claude": ClaudeInstaller,
    "codex": CodexInstaller,
}

def get_installer(agent_name: str) -> BaseAgentInstaller:
    """Retrieve an agent installer instance by name."""
    agent_name = agent_name.lower()
    if agent_name not in _INSTALLER_REGISTRY:
        raise ValueError(f"Unsupported agent '{agent_name}'. Supported agents: {', '.join(SUPPORTED_AGENTS)}")
    return _INSTALLER_REGISTRY[agent_name]()

def get_all_installers() -> List[BaseAgentInstaller]:
    """Retrieve all supported agent installer instances."""
    return [installer_cls() for installer_cls in _INSTALLER_REGISTRY.values()]
