"""Sessions: what agents did, read from harness transcripts into a session index. See ``thearcv2/GLOSSARY.md``.

Public entry point: ``Sessions``. One class per concept — ``Session``, ``Trajectory``,
``SessionIndex`` — plus the normalized records the index stores (``models``).
Exports to harness formats: ``Session.to_jsonl`` / ``export_events``. Subpackage: ``scanner`` (behavioral patterns).
"""

from . import scanner
from .events import UnsupportedConversion
from .export import FORMATS, Export, export_events
from .index import SessionIndex
from .install import INSTALLABLE, InstalledSession, SessionAlreadyInstalled, install_export
from .markdown import evidence_to_markdown
from .models import (
    Event,
    IndexHit,
    Run,
    RunTrace,
    SearchFilters,
    SearchPage,
    SearchQuery,
    SessionMetadata,
    SourceConfig,
    SourceReference,
    SyncReport,
)
from .session import Results, SearchHit, Session, SessionView, Trajectory, TrajectorySplit
from .sessions import Sessions, detect_harness
from .trajectories import split_trajectories

__all__ = [
    "FORMATS",
    "INSTALLABLE",
    "Event",
    "Export",
    "IndexHit",
    "InstalledSession",
    "Results",
    "Run",
    "RunTrace",
    "SearchFilters",
    "SearchHit",
    "SearchPage",
    "SearchQuery",
    "Session",
    "SessionAlreadyInstalled",
    "SessionIndex",
    "SessionMetadata",
    "SessionView",
    "Sessions",
    "SourceConfig",
    "SourceReference",
    "SyncReport",
    "Trajectory",
    "TrajectorySplit",
    "UnsupportedConversion",
    "detect_harness",
    "evidence_to_markdown",
    "export_events",
    "install_export",
    "scanner",
    "split_trajectories",
]
