"""Native session ingestion, normalized records, indexing and queries."""

from thearc.learning.sessions.trajectories import Trajectory, TrajectorySplit, split_trajectories

from . import translation
from .models import (
    Event,
    Harness,
    Run,
    RunTrace,
    SearchFilters,
    SearchHit,
    SearchPage,
    SearchQuery,
    Session,
    SourceConfig,
    SourceReference,
    SyncReport,
)
from .scanner import BehavioralPattern, FrustrationSpike, PatternMatch, PatternScanner, StubbornToolLoop
from .store import HistoryService, SessionStore

__all__ = [
    "BehavioralPattern", "Event", "FrustrationSpike", "Harness", "HistoryService", "PatternMatch", "PatternScanner",
    "Run", "RunTrace", "SearchFilters", "SearchHit", "SearchPage", "SearchQuery", "Session", "SessionStore",
    "SourceConfig", "SourceReference", "StubbornToolLoop", "SyncReport",
    # Session translation subpackage
    "Trajectory", "TrajectorySplit", "split_trajectories",
    "translation",
]
