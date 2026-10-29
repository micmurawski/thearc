"""``/evolve``: evolving a project's skills and instructions from the sessions run in it.

``thearc skills install self-evolve --harness H`` sets a project up (``setup.install_evolve_skill``): its configuration
for H becomes a MetaAgent, and the evolve skill is installed beside it. ``thearc evolve`` runs a round
(``round.run``) with the recipe in the evolve skill, which is given an ``Evolution``.
"""

from .defaults import SCHEMA, USEFULNESS
from .round import (
    Applied,
    Batch,
    Change,
    Evolution,
    Proposal,
    amend,
    apply,
    diff,
    discard,
    ignore,
    rollback,
    run,
    status,
)
from .setup import Setup, adopt, evolve_skill, init_from_remote, install_evolve_skill, uninstall_evolve_skill

__all__ = [
    "SCHEMA",
    "USEFULNESS",
    "Applied",
    "Batch",
    "Change",
    "Evolution",
    "Proposal",
    "Setup",
    "adopt",
    "amend",
    "apply",
    "diff",
    "discard",
    "evolve_skill",
    "ignore",
    "init_from_remote",
    "install_evolve_skill",
    "rollback",
    "run",
    "status",
    "uninstall_evolve_skill",
]
