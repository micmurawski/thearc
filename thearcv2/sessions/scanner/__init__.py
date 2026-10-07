"""Behavioral patterns and the scanner that finds them in indexed sessions."""

from .pattern_scanner import DEFAULT_PATTERNS, PatternScanner
from .patterns import (
    AbandonedFix,
    BehavioralPattern,
    EmptyToolResult,
    FrustrationSpike,
    IgnoredUserInstruction,
    LongSilence,
    PatternMatch,
    SelfOverwrite,
    StubbornToolLoop,
)

__all__ = [
    "DEFAULT_PATTERNS",
    "AbandonedFix",
    "BehavioralPattern",
    "EmptyToolResult",
    "FrustrationSpike",
    "IgnoredUserInstruction",
    "LongSilence",
    "PatternMatch",
    "PatternScanner",
    "SelfOverwrite",
    "StubbornToolLoop",
]
