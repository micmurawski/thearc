"""Legacy flat module paths, centralized so the package tree stays small.

Aliases share the canonical module object (including monkeypatches and pickle
class lookup); they do not copy implementations or emit warnings at import time.
"""

import importlib
import sys

LEGACY_MODULES = {
    "adapters": "sessions.adapters",
    "models": "sessions.models",
    "columnar": "sessions.columnar",
    "service": "sessions.store",
    "exports": "evidence.exports",
    "session_tools": "evidence.tools",
    "reflector": "reflection.engine",
    "reflection_backend": "reflection.backend",
    "reflections": "reflection.flow",
    "codex_reflector": "reflection.providers.codex",
    "claude_reflector": "reflection.providers.claude",
    "antigravity_reflector": "reflection.providers.antigravity",
    "cli_runtime": "reflection.providers.cli",
    "ace_toy": "ace.toy",
    "evaluation": "ace.evaluation",
    "contracts": "runtime.contracts",
    "run": "runtime.journal",
    "handoff": "runtime.handoff",
    "curation.codex": "curation.providers.codex",
}


def install_legacy_imports(namespace: dict) -> None:
    for old, new in LEGACY_MODULES.items():
        module = importlib.import_module(f"thearc.learning.{new}")
        sys.modules[f"thearc.learning.{old}"] = module
        # In particular, don't replace the public session_tools() function.
        if "." not in old:
            namespace.setdefault(old, module)
