"""Public immutable session, reflection, and curation interface."""

from thearc.learning.reflection.runtime import ReflectionError

from .curation import CurationResult, Curator
from .formats import Export, UnsupportedConversion, evidence_to_markdown
from .history import Results, SearchHit, Session, SessionStore, SessionView, Trajectory
from .merger import ConflictResolution, Merger, MergeReport, MergeResult, MergeReview
from .reflection import Reflection, Reflector
from .reflection_tools import ReflectionTools
from .resource_view import ResourceView
from .schemas import (
    CurationBaseModel,
    CurationReport,
    OutputValidationError,
    ReferencedReflection,
    ReflectionBaseModel,
    ResourceAssessment,
    ResourceAssessmentReport,
    ResourceReference,
    ResourceTag,
    TaggedReflection,
)
from .sessions import Sessions

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
    "ReferencedReflection",
    "Reflection",
    "ReflectionBaseModel",
    "ReflectionError",
    "ReflectionTools",
    "Reflector",
    "ResourceAssessment",
    "ResourceAssessmentReport",
    "ResourceReference",
    "ResourceTag",
    "ResourceView",
    "Results",
    "SearchHit",
    "Session",
    "SessionStore",
    "SessionView",
    "Sessions",
    "TaggedReflection",
    "Trajectory",
    "UnsupportedConversion",
    "evidence_to_markdown",
]
