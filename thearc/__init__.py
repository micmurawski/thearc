"""thearc - Agent-agnostic plugin and skill installer system for Codex, Claude, and Antigravity."""

from thearc.models.agent import (
    MCP,
    AgentResource,
    ContextDocument,
    Hook,
    MetaAgent,
    Operation,
    ResourceTarget,
    Skill,
)

__version__ = "0.1.0"
__all__ = [
    "MCP",
    "AgentResource",
    "ContextDocument",
    "Hook",
    "MetaAgent",
    "Operation",
    "ResourceTarget",
    "Skill",
]
