"""Sessions: what agents did, read from harness transcripts into a session index. See ``thearcv2/GLOSSARY.md``.

Public entry point: ``Sessions``. One class per concept — ``Session``, ``Trajectory``,
``SessionIndex`` — plus the normalized records the index stores (``models``).
Subpackages: ``scanner`` (behavioral patterns) and ``translation`` (transcript conversion).
"""

from . import scanner, translation
from .formats import Export, UnsupportedConversion, evidence_to_markdown
from .index import SessionIndex
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
    "Event",
    "Export",
    "IndexHit",
    "Results",
    "Run",
    "RunTrace",
    "SearchFilters",
    "SearchHit",
    "SearchPage",
    "SearchQuery",
    "Session",
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
    "scanner",
    "split_trajectories",
    "translation",
]
