"""Import boundaries and compatibility for the organized learning package."""

import importlib
import pickle
import subprocess
import sys

import pytest

from thearc import learning
from thearc.learning._compat import LEGACY_MODULES


@pytest.mark.parametrize(("legacy", "canonical"), LEGACY_MODULES.items())
def test_legacy_module_is_canonical_module(legacy, canonical):
    assert importlib.import_module(f"thearc.learning.{legacy}") is importlib.import_module(
        f"thearc.learning.{canonical}"
    )


@pytest.mark.parametrize(
    ("package", "name"),
    [
        ("sessions", "SessionStore"),
        ("sessions", "HistoryService"),
        ("evidence", "EvidenceSnapshot"),
        ("ace", "Reflection"),
        ("reflection", "AgentReflector"),
        ("reflection", "CodexReflector"),
        ("reflection", "ClaudeReflector"),
        ("reflection", "AntigravityReflector"),
    ],
)
def test_focused_packages_share_facade_types(package, name):
    assert getattr(importlib.import_module(f"thearc.learning.{package}"), name) is getattr(
        learning, name
    )


def test_legacy_alias_shares_monkeypatches(monkeypatch):
    legacy = importlib.import_module("thearc.learning.claude_reflector")
    canonical = importlib.import_module("thearc.learning.reflection.providers.claude")
    sentinel = object()
    monkeypatch.setattr(legacy, "ClaudeReflector", sentinel)
    assert canonical.ClaudeReflector is sentinel
    assert callable(learning.session_tools)


@pytest.mark.parametrize(
    ("module", "name"),
    [("models", "Session"), ("sessions", "SessionStore"), ("ace", "Reflection")],
)
def test_legacy_pickle_class_lookup(module, name):
    # Only a fixed class reference, never untrusted session data.
    payload = f"cthearc.learning.{module}\n{name}\n.".encode()
    assert pickle.loads(payload) is getattr(importlib.import_module(f"thearc.learning.{module}"), name)


@pytest.mark.parametrize(
    "first_import",
    ["sessions.store", "evidence.tools", "reflection.providers.claude", "runtime.handoff", "ace.pipeline"],
)
def test_fresh_import_without_optional_sdks(first_import):
    code = f"""
import importlib
import sys
importlib.import_module('thearc.learning.{first_import}')
assert 'openai_codex' not in sys.modules
assert 'google.antigravity' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("backend,prefix", [("antigravity", "Antigravity"), ("claude", "Claude"), ("codex", "Codex")])
def test_curator_provider_layout_and_exports(backend, prefix):
    package = importlib.import_module("thearc.learning.curation")
    providers = importlib.import_module("thearc.learning.curation.providers")
    module = importlib.import_module(f"thearc.learning.curation.providers.{backend}")
    cls = getattr(module, f"{prefix}Curator")
    assert cls.__module__ == module.__name__
    assert getattr(package, cls.__name__) is cls
    assert getattr(providers, cls.__name__) is cls


def test_old_codex_curator_path_still_imports():
    from thearc.learning.curation.codex import CodexCurator

    from thearc.learning.curation.providers.codex import CodexCurator as CanonicalCurator

    assert CodexCurator is CanonicalCurator
