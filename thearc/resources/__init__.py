"""Public immutable session, reflection, and curation interface."""

from thearc.learning.reflection.runtime import ReflectionError

from .curation import CurationResult, Curator
from .formats import Export, UnsupportedConversion, evidence_to_markdown
from .history import Results, SearchHit, Session, SessionStore, SessionView, Trajectory
from .merger import ConflictResolution, Merger, MergeReport, MergeResult, MergeReview
from .reflection import Reflection, Reflector
from .schemas import (
    CurationBaseModel,
    CurationReport,
    OutputValidationError,
    ReflectionBaseModel,
    ResourceAssessment,
    ResourceAssessmentReport,
)

__all__ = [
    "ConflictResolution",
    "CurationBaseModel",
    "CurationReport",
    "CurationResult",
    "Curator",
    "Export",
    "MergeReport",
    "MergeResult",
    "MergeReview",
    "Merger",
    "OutputValidationError",
    "Reflection",
    "ReflectionBaseModel",
    "ReflectionError",
    "Reflector",
    "ResourceAssessment",
    "ResourceAssessmentReport",
    "Results",
    "SearchHit",
    "Session",
    "SessionStore",
    "SessionView",
    "Trajectory",
    "UnsupportedConversion",
    "evidence_to_markdown",
]
