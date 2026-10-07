"""MetaAgent configuration, its journal, and ratings. See ``thearcv2/GLOSSARY.md``."""

from .agent import MCP, AgentResource, ContextDocument, Hook, MetaAgent, Skill
from .changes import detect_changes
from .files import AgentFiles, FileReference, Span, diff_files
from .git import Commit, GitError, agent_at, record_commits, workspace_commits
from .harness import HARNESSES, HarnessLayout, Installation, InstallConflict, install, read_installation
from .journal import Added, Change, Entry, Journal, Lineage, Modified, Moved, Rated, Removed
from .ratings import (
    CategoricalScale,
    CountSummary,
    LabelScale,
    NumericScale,
    NumericSummary,
    RatingPolicy,
    RatingScale,
    Summary,
    count_all,
    since_last_modification,
)
from .reflection import ReflectionItem, ReflectionResult, ReflectionSchema, Rejection
from .targets import ResourceTarget
from .views import agent_outline, annotated_files
from .workspace import read_workspace, workspace_files, write_workspace

__all__ = [
    "HARNESSES",
    "MCP",
    "Added",
    "AgentFiles",
    "AgentResource",
    "CategoricalScale",
    "Change",
    "Commit",
    "ContextDocument",
    "CountSummary",
    "Entry",
    "FileReference",
    "GitError",
    "HarnessLayout",
    "Hook",
    "InstallConflict",
    "Installation",
    "Journal",
    "LabelScale",
    "Lineage",
    "MetaAgent",
    "Modified",
    "Moved",
    "NumericScale",
    "NumericSummary",
    "Rated",
    "RatingPolicy",
    "RatingScale",
    "ReflectionItem",
    "ReflectionResult",
    "ReflectionSchema",
    "Rejection",
    "Removed",
    "ResourceTarget",
    "Skill",
    "Span",
    "Summary",
    "agent_at",
    "agent_outline",
    "annotated_files",
    "count_all",
    "detect_changes",
    "diff_files",
    "install",
    "read_installation",
    "read_workspace",
    "record_commits",
    "since_last_modification",
    "workspace_commits",
    "workspace_files",
    "write_workspace",
]
