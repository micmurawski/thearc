"""MetaAgent configuration, its journal, and ratings. See ``GLOSSARY.md``."""

from .agent import MCP, AgentResource, ContextDocument, Extension, Hook, MetaAgent, Skill
from .changes import detect_changes
from .files import AgentFiles, FileReference, Span, diff_files
from .git import Commit, GitError, agent_at, record_commits, workspace_commits
from .harness import HARNESSES, HarnessLayout, Installation, InstallConflict, install, read_installation
from .journal import Added, Change, Entry, Journal, Kept, Lineage, Modified, Moved, Rated, Removed
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
    including_subsections,
    since_last_modification,
)
from .reflection import Ref, ReflectionItem, ReflectionResult, ReflectionSchema, Rejection
from .targets import ResourceTarget
from .views import agent_outline, annotated_files
from .workspace import init_workspace, read_workspace, workspace_files, write_workspace

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
    "Extension",
    "FileReference",
    "GitError",
    "HarnessLayout",
    "Hook",
    "InstallConflict",
    "Installation",
    "Journal",
    "Kept",
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
    "Ref",
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
    "including_subsections",
    "init_workspace",
    "install",
    "read_installation",
    "read_workspace",
    "record_commits",
    "since_last_modification",
    "workspace_commits",
    "workspace_files",
    "write_workspace",
]
