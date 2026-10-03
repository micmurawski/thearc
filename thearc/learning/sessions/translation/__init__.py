"""Session translation module.

Convert AI CLI session files between Pi, Antigravity (agy), Codex, Claude Code,
and OpenCode formats. Built on patterns from vibheksoni/session-export.

Supported providers:
    - ``pi``         — Pi coding agent (JSONL append-only, cwd-encoded dirs)
    - ``agy``        — Antigravity / Google Deepmind CLI (transcript.jsonl brain)
    - ``codex``      — OpenAI Codex CLI (JSONL rollout under date tree)
    - ``claude``     — Claude Code / Anthropic CLI (JSONL transcript, cwd-sanitized dirs)
    - ``opencode``   — OpenCode / SST (official export/import JSON, ``ses_`` IDs)

Usage::

    from thearc.learning.sessions.translation import SessionTranslator

    t = SessionTranslator()
    plan = t.plan("codex", "pi", session_id="<uuid>")
    t.write(plan)
"""

from .builders import (
    AntigravityRecordBuilder,
    ClaudeRecordBuilder,
    CodexRecordBuilder,
    MarkdownRecordBuilder,
    OpenCodeRecordBuilder,
    PiRecordBuilder,
)
from .extractors import MessageExtractor
from .models import (
    ConversionPlan,
    NativeSession,
    SessionSummary,
    TextMessage,
)
from .stores import (
    AntigravityStore,
    ClaudeStore,
    CodexStore,
    MarkdownStore,
    OpenCodeStore,
    PiStore,
)
from .translator import SessionTranslator

__all__ = [
    "AntigravityRecordBuilder",
    "AntigravityStore",
    "ClaudeRecordBuilder",
    "ClaudeStore",
    "CodexRecordBuilder",
    "CodexStore",
    "ConversionPlan",
    "MarkdownRecordBuilder",
    "MarkdownStore",
    "MessageExtractor",
    "NativeSession",
    "OpenCodeRecordBuilder",
    "OpenCodeStore",
    "PiRecordBuilder",
    "PiStore",
    "SessionSummary",
    "SessionTranslator",
    "TextMessage",
]
