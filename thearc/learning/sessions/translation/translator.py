"""High-level SessionTranslator facade.

Wires together stores, extractors, and builders to provide a clean API for
converting sessions between Pi, Antigravity (agy), Codex, Claude, and OpenCode.

Example::

    from thearc.learning.sessions.translation import SessionTranslator

    t = SessionTranslator()

    # List all sessions for a provider
    summaries = t.list_sessions("codex")

    # Plan a conversion (dry-run, no side effects)
    plan = t.plan("codex", "pi", session_id="<uuid>")
    print(plan.destination)
    for rec in plan.records:
        print(rec)

    # Write the output
    t.write(plan)

    # Or do it all in one step
    t.convert("claude", "agy", session_id="<uuid>", write=True)
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

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
    is_uuid,
    now_iso,
    opencode_id,
)
from .stores import (
    AntigravityStore,
    ClaudeStore,
    CodexStore,
    MarkdownStore,
    OpenCodeStore,
    PiStore,
)

# Supported provider names
PROVIDERS = frozenset({"pi", "agy", "antigravity", "codex", "claude", "opencode", "markdown", "md"})


def _normalize_provider(name: str) -> str:
    n = name.lower().strip()
    if n in ("agy", "antigravity", "gemini"):
        return "agy"
    if n in ("claude", "claudecode", "claude-code"):
        return "claude"
    if n in ("codex", "openai-codex"):
        return "codex"
    if n in ("pi", "pi-agent"):
        return "pi"
    if n in ("opencode", "open-code"):
        return "opencode"
    if n in ("markdown", "md"):
        return "markdown"
    raise ValueError(
        f"Unknown provider {name!r}. Choose from: {sorted(PROVIDERS)}"
    )


class SessionTranslator:
    """Unified session translation facade for Pi, agy, Codex, Claude, and OpenCode.

    Parameters
    ----------
    codex_home:
        Override ``~/.codex`` (Codex).
    pi_home:
        Override ``~/.pi`` (Pi).
    claude_home:
        Override ``~/.claude`` (Claude).
    opencode_data_home:
        Override OpenCode data dir.
    agy_brain_dir:
        Override Antigravity brain dir (``~/.gemini/antigravity-cli/brain``).
    markdown_export_dir:
        Override default Markdown export dir.
    preserve_ids:
        When ``True`` (default), keep the original session ID in the output.
        Set to ``False`` to generate a fresh ID for the target provider.
    """

    def __init__(
        self,
        *,
        codex_home: Path | None = None,
        pi_home: Path | None = None,
        claude_home: Path | None = None,
        opencode_data_home: Path | None = None,
        agy_brain_dir: Path | None = None,
        markdown_export_dir: Path | None = None,
        preserve_ids: bool = True,
    ) -> None:
        self._codex = CodexStore(codex_home)
        self._pi = PiStore(pi_home)
        self._claude = ClaudeStore(claude_home)
        self._opencode = OpenCodeStore(opencode_data_home)
        self._agy = AntigravityStore(agy_brain_dir)
        self._markdown = MarkdownStore(markdown_export_dir)
        self._extractor = MessageExtractor()
        self._preserve_ids = preserve_ids

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def list_sessions(self, provider: str) -> list[SessionSummary]:
        """Return summary metadata for all sessions of the given *provider*."""
        store = self._store(provider)
        return store.list()

    def load(self, provider: str, session_id: str) -> NativeSession:
        """Load a native session by provider and session ID."""
        return self._store(provider).load(session_id)

    def plan(
        self,
        source_provider: str,
        target_provider: str,
        *,
        session_id: str,
        target_id: str | None = None,
    ) -> ConversionPlan:
        """Plan a conversion without writing any files.

        Returns a :class:`ConversionPlan` with the translated records and the
        intended destination path.  Call :meth:`write` to materialise it.
        """
        src_prov = _normalize_provider(source_provider)
        tgt_prov = _normalize_provider(target_provider)
        source = self._store(src_prov).load(session_id)
        return self._build_plan(source, tgt_prov, target_id=target_id)

    def plan_from_path(
        self,
        source_provider: str,
        target_provider: str,
        *,
        path: Path,
        target_id: str | None = None,
    ) -> ConversionPlan:
        """Plan a conversion from an explicit file path."""
        src_prov = _normalize_provider(source_provider)
        tgt_prov = _normalize_provider(target_provider)
        source = self._store(src_prov).load_path(path)
        return self._build_plan(source, tgt_prov, target_id=target_id)

    def write(self, plan: ConversionPlan, *, overwrite: bool = False) -> Path:
        """Write a :class:`ConversionPlan` to disk and return the destination path."""
        target_store = self._store(plan.target_provider)
        target_store.write(plan.destination, plan.records, overwrite=overwrite)
        return plan.destination

    def convert(
        self,
        source_provider: str,
        target_provider: str,
        *,
        session_id: str,
        target_id: str | None = None,
        write: bool = False,
        overwrite: bool = False,
    ) -> ConversionPlan:
        """Plan and optionally write a session conversion in one step.

        Parameters
        ----------
        source_provider:
            Provider name of the source session (``"codex"``, ``"pi"``, ``"claude"``,
            ``"opencode"``, ``"agy"``).
        target_provider:
            Provider name to convert to.
        session_id:
            Session ID to load from the source provider's store.
        target_id:
            Optional explicit ID to assign to the output session.
        write:
            If ``True``, write the output to disk immediately.
        overwrite:
            If ``True``, overwrite existing output files.

        Returns
        -------
        ConversionPlan
            The plan, regardless of whether it was written.
        """
        plan = self.plan(
            source_provider, target_provider,
            session_id=session_id, target_id=target_id,
        )
        if write:
            self.write(plan, overwrite=overwrite)
        return plan

    def convert_all(
        self,
        source_provider: str,
        target_provider: str,
        *,
        write: bool = False,
        overwrite: bool = False,
        skip_errors: bool = True,
    ) -> list[ConversionPlan]:
        """Convert all sessions from *source_provider* to *target_provider*.

        Parameters
        ----------
        skip_errors:
            If ``True`` (default), log errors and continue rather than raising.
        """
        src_prov = _normalize_provider(source_provider)
        tgt_prov = _normalize_provider(target_provider)
        summaries = self._store(src_prov).list()
        plans: list[ConversionPlan] = []
        for summary in summaries:
            try:
                source = self._store(src_prov).load(summary.session_id)
                plan = self._build_plan(source, tgt_prov)
                if write:
                    self.write(plan, overwrite=overwrite)
                plans.append(plan)
            except Exception as exc:
                if skip_errors:
                    import sys
                    print(
                        f"warning: skipped {src_prov}:{summary.session_id} → {tgt_prov}: {exc}",
                        file=sys.stderr,
                    )
                else:
                    raise
        return plans

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    def _store(self, provider: str):
        p = _normalize_provider(provider)
        if p == "codex":
            return self._codex
        elif p == "pi":
            return self._pi
        elif p == "claude":
            return self._claude
        elif p == "opencode":
            return self._opencode
        elif p == "agy":
            return self._agy
        elif p == "markdown":
            return self._markdown
        else:
            raise ValueError(f"Unhandled provider: {p!r}")

    def _extract(self, source: NativeSession) -> list:
        p = _normalize_provider(source.provider)
        if p == "codex":
            return self._extractor.from_codex(source)
        elif p == "pi":
            return self._extractor.from_pi(source)
        elif p == "claude":
            return self._extractor.from_claude(source)
        elif p == "opencode":
            return self._extractor.from_opencode(source)
        elif p == "agy":
            return self._extractor.from_agy(source)
        else:
            raise ValueError(f"Cannot extract from unknown provider: {p!r}")

    def _make_target_id(self, source: NativeSession, target_provider: str) -> str:
        if self._preserve_ids:
            if target_provider == "opencode":
                # OpenCode requires ses_-prefixed IDs
                if source.session_id.startswith("ses_"):
                    return source.session_id
                return opencode_id("ses", source.timestamp or now_iso())
            if target_provider in ("codex", "pi", "claude", "agy") and is_uuid(source.session_id):
                return source.session_id
        return str(uuid4())

    def _build_plan(
        self,
        source: NativeSession,
        target_provider: str,
        *,
        target_id: str | None = None,
    ) -> ConversionPlan:
        messages = self._extract(source)
        resolved_id = target_id or self._make_target_id(source, target_provider)
        timestamp = source.timestamp or now_iso()
        cwd = source.cwd or ""

        if target_provider == "codex":
            builder = CodexRecordBuilder()
            records = builder.build(resolved_id, cwd, timestamp, messages)
            destination = self._codex.destination_path(resolved_id, timestamp)
        elif target_provider == "pi":
            builder = PiRecordBuilder()
            records = builder.build(resolved_id, cwd, timestamp, messages)
            destination = self._pi.destination_path(resolved_id, cwd, timestamp)
        elif target_provider == "claude":
            builder = ClaudeRecordBuilder()
            records = builder.build(resolved_id, cwd, timestamp, messages)
            destination = self._claude.destination_path(resolved_id, cwd)
        elif target_provider == "opencode":
            builder = OpenCodeRecordBuilder()
            records = builder.build(resolved_id, cwd, timestamp, messages)
            destination = self._opencode.destination_path(resolved_id)
        elif target_provider == "agy":
            builder = AntigravityRecordBuilder()
            records = builder.build(resolved_id, cwd, timestamp, messages)
            destination = self._agy.destination_path(resolved_id, cwd)
        elif target_provider == "markdown":
            builder = MarkdownRecordBuilder()
            records = builder.build(resolved_id, cwd, timestamp, messages)
            destination = self._markdown.destination_path(resolved_id)
        else:
            raise ValueError(f"Cannot build records for unknown provider: {target_provider!r}")

        return ConversionPlan(source=source, destination=destination, records=records, target_provider=target_provider)
