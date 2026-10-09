"""Harnesses: the agent environments thearc reads sessions from and installs MetaAgents into."""

from typing import Literal

Harness = Literal["claude", "codex", "antigravity", "pi", "opencode"]
HARNESSES: tuple[Harness, ...] = ("claude", "codex", "antigravity", "pi", "opencode")
