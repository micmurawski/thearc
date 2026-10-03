from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

# Target Agent Constants
AGENT_ANTIGRAVITY = "antigravity"
AGENT_CLAUDE = "claude"
AGENT_CODEX = "codex"
AGENT_PI = "pi"
SUPPORTED_AGENTS = [AGENT_ANTIGRAVITY, AGENT_CLAUDE, AGENT_CODEX, AGENT_PI]


def normalize_agent_name(agent: str) -> str:
    """Normalize agent implementation names including aliases and typos."""
    ag = agent.lower().strip()
    if ag in ("claudecode", "claude"):
        return AGENT_CLAUDE
    elif ag in ("anitgravity", "antigravity"):
        return AGENT_ANTIGRAVITY
    elif ag == "codex":
        return AGENT_CODEX
    elif ag == "pi":
        return AGENT_PI
    return ag


# Default Paths Configuration
def get_user_home() -> Path:
    return Path.home()


def get_global_agent_paths(agent: str) -> dict[str, Path]:
    agent = normalize_agent_name(agent)
    home = get_user_home()
    if agent == AGENT_ANTIGRAVITY:
        base = home / ".gemini" / "config"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "commands",
            "hooks": base / "hooks",
            "mcp": base / "mcp_config.json",
            "history": home / ".gemini" / "antigravity-cli" / "brain",
        }
    elif agent == AGENT_CLAUDE:
        base = home / ".claude"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "commands",
            "hooks": base / "hooks",
            "mcp": base / "mcp.json",
            "history": base / "history",
        }
    elif agent == AGENT_CODEX:
        base = home / ".codex"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "prompts",
            "hooks": base / "hooks",
            "mcp": base / "mcp.json",
            "history": base / "history",
        }
    elif agent == AGENT_PI:
        base = home / ".pi"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "prompts",
            "hooks": base / "hooks",
            "mcp": base / "mcp.json",
            "history": base / "history",
        }
    else:
        raise ValueError(f"Unknown agent: {agent}")


def get_local_agent_paths(project_dir: Path, agent: str) -> dict[str, Path]:
    agent = normalize_agent_name(agent)
    project_dir = Path(project_dir).resolve()
    if agent == AGENT_ANTIGRAVITY:
        base = project_dir / ".agents"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "commands",
            "hooks": base / "hooks",
            "mcp": base / "mcp_config.json",
        }
    elif agent == AGENT_CLAUDE:
        base = project_dir / ".claude"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "commands",
            "hooks": base / "hooks",
            "mcp": base / "mcp.json",
        }
    elif agent == AGENT_CODEX:
        base = project_dir / ".codex"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "prompts",
            "hooks": base / "hooks",
            "mcp": base / "mcp.json",
        }
    elif agent == AGENT_PI:
        base = project_dir / ".pi"
        return {
            "base": base,
            "skills": base / "skills",
            "rules": base / "rules",
            "commands": base / "prompts",
            "hooks": base / "hooks",
            "mcp": base / "mcp.json",
        }
    else:
        raise ValueError(f"Unknown agent: {agent}")


def load_thearc_config(project_dir: Path | None = None, agent: str | None = None) -> dict[str, Any]:
    """
    Search for and load configuration settings from .thearc.toml files.
    Checks:
    1. Local agent dir: <project_dir>/.<agent>/.thearc.toml or <project_dir>/.agents/.thearc.toml
    2. Global agent dir: ~/.claude/.thearc.toml, ~/.gemini/config/.thearc.toml, etc.
    """
    p_dir = Path(project_dir or Path(".")).resolve()
    config_paths = []

    # Local project search
    if agent:
        local_base = get_local_agent_paths(p_dir, agent)["base"]
        config_paths.append(local_base / ".thearc.toml")
    else:
        for ag in SUPPORTED_AGENTS:
            config_paths.append(get_local_agent_paths(p_dir, ag)["base"] / ".thearc.toml")

    config_paths.append(p_dir / ".thearc.toml")

    # Global search
    if agent:
        global_base = get_global_agent_paths(agent)["base"]
        config_paths.append(global_base / ".thearc.toml")
    else:
        for ag in SUPPORTED_AGENTS:
            config_paths.append(get_global_agent_paths(ag)["base"] / ".thearc.toml")

    merged_config: dict[str, Any] = {}
    for c_path in reversed(config_paths):  # Local overrides global
        try:
            with c_path.open("rb") as config_file:
                data = tomllib.load(config_file)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        merged_config.update(data)

    return merged_config
