import os
from pathlib import Path
from typing import Dict, Any

# Target Agent Constants
AGENT_ANTIGRAVITY = "antigravity"
AGENT_CLAUDE = "claude"
AGENT_CODEX = "codex"
SUPPORTED_AGENTS = [AGENT_ANTIGRAVITY, AGENT_CLAUDE, AGENT_CODEX]

# Default Paths Configuration
def get_user_home() -> Path:
    return Path.home()

def get_global_agent_paths(agent: str) -> Dict[str, Path]:
    home = get_user_home()
    if agent == AGENT_ANTIGRAVITY:
        base = home / ".gemini" / "config"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "commands",
            "history": home / ".gemini" / "antigravity-cli" / "brain",
        }
    elif agent == AGENT_CLAUDE:
        base = home / ".claude"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "commands",
            "history": base / "history",
        }
    elif agent == AGENT_CODEX:
        base = home / ".codex"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "prompts",
            "history": base / "history",
        }
    else:
        raise ValueError(f"Unknown agent: {agent}")

def get_local_agent_paths(project_dir: Path, agent: str) -> Dict[str, Path]:
    project_dir = Path(project_dir).resolve()
    if agent == AGENT_ANTIGRAVITY:
        base = project_dir / ".agents"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "commands",
        }
    elif agent == AGENT_CLAUDE:
        base = project_dir / ".claude"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "commands",
        }
    elif agent == AGENT_CODEX:
        base = project_dir / ".codex"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "prompts",
        }
    else:
        raise ValueError(f"Unknown agent: {agent}")
