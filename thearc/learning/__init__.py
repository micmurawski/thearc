"""Session-driven agent learning: trace indexing, reflection, evaluation, and ACE."""

from thearc.learning.ace.evaluation import EvaluationResult, TaskScore, compare_agents
from thearc.learning.ace.pipeline import (
    AceConfig,
    AcePipeline,
    AceQuery,
    AceRunResult,
    Curation,
    HistorySessionMaterializer,
    HistorySessionSelector,
    Reflection,
    ReflectionEvidence,
    ReflectionItem,
    ReflectionRating,
    SessionBundle,
    UpdateResult,
    apply_curations,
    build_ace_flow,
)
from thearc.learning.ace.toy import (
    ChangeJournal,
    ChangeReason,
    JournalEntry,
    RankProposal,
    ToyCurator,
    ToyReflector,
    run_toy_flow,
)
from thearc.learning.evidence.exports import ContextExport, render_context, write_files
from thearc.learning.evidence.snapshot import EvidenceSnapshot
from thearc.learning.evidence.tools import EvidenceToolError, EvidenceTools, RetrievalLimits, session_tools
from thearc.learning.reflection.backend import ReflectionBackend, ReflectionResponse, ReflectionRunner
from thearc.learning.reflection.engine import AgentReflector, ReflectionError, ReflectorConfig
from thearc.learning.reflection.flow import build_reflection_flow, run_reflections
from thearc.learning.reflection.providers.antigravity import AntigravityReflector, AntigravityReflectorConfig
from thearc.learning.reflection.providers.claude import ClaudeReflector, ClaudeReflectorConfig
from thearc.learning.reflection.providers.codex import CodexReflector, CodexReflectorConfig
from thearc.learning.runtime.contracts import (
    ContextSnapshot,
    Episode,
    EpisodeOutcome,
    EpisodeQuery,
    RunCheckpoint,
)
from thearc.learning.runtime.handoff import HandoffMode, HandoffPlan, load_handoff, prepare_handoff, save_handoff
from thearc.learning.runtime.journal import AdaptationRun, CheckpointStore, RunJournal, RunJournalRecord
from thearc.learning.sessions.models import (
    Event,
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
from thearc.learning.sessions.store import SessionStore

from ._compat import install_legacy_imports as _install_legacy_imports

HistoryService = SessionStore  # Compatibility alias, not a second implementation.

__all__ = [
    "AceConfig",
    "AcePipeline",
    "AceQuery",
    "AceRunResult",
    "AdaptationRun",
    "AgentReflector",
    "AntigravityReflector",
    "AntigravityReflectorConfig",
    "ChangeJournal",
    "ChangeReason",
    "CheckpointStore",
    "ClaudeReflector",
    "ClaudeReflectorConfig",
    "CodexReflector",
    "CodexReflectorConfig",
    "ContextExport",
    "ContextSnapshot",
    "Curation",
    "Episode",
    "EpisodeOutcome",
    "EpisodeQuery",
    "EvaluationResult",
    "Event",
    "EvidenceSnapshot",
    "EvidenceToolError",
    "EvidenceTools",
    "HandoffMode",
    "HandoffPlan",
    "HistoryService",
    "HistorySessionMaterializer",
    "HistorySessionSelector",
    "JournalEntry",
    "RankProposal",
    "Reflection",
    "ReflectionBackend",
    "ReflectionError",
    "ReflectionEvidence",
    "ReflectionItem",
    "ReflectionRating",
    "ReflectionResponse",
    "ReflectionRunner",
    "ReflectorConfig",
    "RetrievalLimits",
    "Run",
    "RunCheckpoint",
    "RunJournal",
    "RunJournalRecord",
    "RunTrace",
    "SearchFilters",
    "SearchHit",
    "SearchPage",
    "SearchQuery",
    "Session",
    "SessionBundle",
    "SessionStore",
    "SourceConfig",
    "SourceReference",
    "SyncReport",
    "TaskScore",
    "ToyCurator",
    "ToyReflector",
    "UpdateResult",
    "apply_curations",
    "build_ace_flow",
    "build_reflection_flow",
    "compare_agents",
    "load_handoff",
    "prepare_handoff",
    "render_context",
    "run_reflections",
    "run_toy_flow",
    "save_handoff",
    "session_tools",
    "write_files",
]

# Keep old dotted imports pointing at the canonical module objects.
_install_legacy_imports(globals())
