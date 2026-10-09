"""Reflection: judging a MetaAgent's files against evidence. See ``GLOSSARY.md``."""

from .reflector import INSTRUCTIONS, Reflection, Reflector
from .runtimes import (
    RUNTIMES,
    AntigravityRuntime,
    ClaudeRuntime,
    CodexRuntime,
    Conversation,
    PiRuntime,
    ReflectionError,
    Runtime,
    ToolEndpoint,
)
from .submission import Submission

__all__ = [
    "INSTRUCTIONS",
    "RUNTIMES",
    "AntigravityRuntime",
    "ClaudeRuntime",
    "CodexRuntime",
    "Conversation",
    "PiRuntime",
    "Reflection",
    "ReflectionError",
    "Reflector",
    "Runtime",
    "Submission",
    "ToolEndpoint",
]
