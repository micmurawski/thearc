"""One public resource API, with no legacy import aliases."""

import importlib
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "name",
    [
        "service",
        "models",
        "reflector",
        "codex_reflector",
        "reflections",
        "_compat",
        "ace.pipeline",
        "ace.toy",
        "reflection.engine",
        "reflection.factory",
        "reflection.handoff",
        "reflection.flow",
        "curation.factory",
    ],
)
def test_removed_import_paths_fail(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("thearc.learning." + name)


def test_public_import_does_not_load_optional_sdks():
    code = (
        "import sys; from thearc.resources import Session, Reflector, Curator; "
        "assert 'openai_codex' not in sys.modules; assert 'google.antigravity' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
