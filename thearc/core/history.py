from pathlib import Path
from typing import Optional, List
import os
import glob
from thearc.config import get_global_agent_paths, AGENT_ANTIGRAVITY, AGENT_CLAUDE, AGENT_CODEX

def find_latest_transcript(
    agent: Optional[str] = None,
    custom_path: Optional[str] = None
) -> Path:
    """
    Find the latest transcript or history file for the agent.

    :param agent: Agent name ('antigravity', 'claude', 'codex', or None for auto)
    :param custom_path: User-provided explicit file path
    :return: Path to transcript file
    """
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.exists():
            return p
        else:
            raise FileNotFoundError(f"Specified transcript file not found at: {custom_path}")

    candidates: List[Path] = []

    # Search Antigravity brain logs
    ag_brain = get_global_agent_paths(AGENT_ANTIGRAVITY)["history"]
    if ag_brain.exists():
        for log_file in ag_brain.glob("**/*.jsonl"):
            if log_file.is_file():
                candidates.append(log_file)

    # Search Claude history
    claude_hist = get_global_agent_paths(AGENT_CLAUDE)["history"]
    if claude_hist.exists():
        for h_file in claude_hist.glob("**/*"):
            if h_file.is_file():
                candidates.append(h_file)

    # Search Codex history
    codex_hist = get_global_agent_paths(AGENT_CODEX)["history"]
    if codex_hist.exists():
        for h_file in codex_hist.glob("**/*"):
            if h_file.is_file():
                candidates.append(h_file)

    # Local project level history fallbacks
    for local_pattern in [".agents/logs/*.jsonl", ".claude/history/*", ".codex/history/*"]:
        for match in glob.glob(local_pattern, recursive=True):
            p = Path(match).resolve()
            if p.is_file():
                candidates.append(p)

    if candidates:
        # Sort by modification time descending
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    raise FileNotFoundError(
        "No chat context/transcript history files found automatically. "
        "Please specify a transcript file path using --file <path>."
    )
