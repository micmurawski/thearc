from __future__ import annotations

from thearc.agents.antigravity import AntigravityInstaller
from thearc.agents.base import BaseAgentInstaller
from thearc.agents.claude import ClaudeInstaller
from thearc.agents.codex import CodexInstaller
from thearc.agents.pi import PiInstaller
from thearc.config import SUPPORTED_AGENTS, normalize_agent_name

_INSTALLER_REGISTRY: dict[str, type[BaseAgentInstaller]] = {
    "antigravity": AntigravityInstaller,
    "claude": ClaudeInstaller,
    "codex": CodexInstaller,
    "pi": PiInstaller,
}

def get_installer(agent_name: str) -> BaseAgentInstaller:
    """Retrieve an agent installer instance by name."""
    norm_name = normalize_agent_name(agent_name)
    if norm_name not in _INSTALLER_REGISTRY:
        raise ValueError(f"Unsupported agent '{agent_name}'. Supported agents: {', '.join(SUPPORTED_AGENTS)}")
    return _INSTALLER_REGISTRY[norm_name]()

def get_all_installers() -> list[BaseAgentInstaller]:
    """Retrieve all supported agent installer instances."""
    return [installer_cls() for installer_cls in _INSTALLER_REGISTRY.values()]
