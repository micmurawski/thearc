"""CLI assembly and hidden aliases for earlier command paths."""

from copy import copy

from thearc.learning.evidence.cli import evidence
from thearc.learning.runtime.cli import handoff_commands
from thearc.learning.sessions.cli import sessions


def hidden_alias(command, name=None):
    """Hide an alias without changing the canonical command's help visibility."""
    alias = copy(command)
    alias.hidden = True
    if name is not None:
        alias.name = name
    return alias


# Keep the old index option placement and callbacks for existing scripts.
for name in ("snapshot", "export", "inspect"):
    sessions.add_command(hidden_alias(evidence.commands[name]))
sessions.add_command(hidden_alias(handoff_commands))
history = hidden_alias(sessions, "history")

__all__ = ["evidence", "handoff_commands", "history", "sessions"]
