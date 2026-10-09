"""Behavioral patterns and the scanner that finds them in indexed sessions."""

from .algebra import AllOf, AnyOf
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
from .shell import ShellCommand
from .skills import SkillUsage

__all__ = [
    "DEFAULT_PATTERNS",
    "AbandonedFix",
    "AllOf",
    "AnyOf",
    "BehavioralPattern",
    "EmptyToolResult",
    "FrustrationSpike",
    "IgnoredUserInstruction",
    "LongSilence",
    "PatternMatch",
    "PatternScanner",
    "SelfOverwrite",
    "ShellCommand",
    "SkillUsage",
    "StubbornToolLoop",
]
