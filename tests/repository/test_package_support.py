"""Packaging declarations and compatibility contracts independent of optional SDKs."""

import json
import tomllib
from importlib.resources import files

from tests.paths import REPOSITORY_ROOT
from thearc.learning.runtime.handoff import HandoffMode


def test_visualization_assets_declared_and_present():
    project = REPOSITORY_ROOT / "pyproject.toml"
    config = tomllib.loads(project.read_text())
    assert set(config["tool"]["setuptools"]["package-data"]["thearc.flow"]) == {"viz.css", "viz.js"}
    for asset in ("viz.css", "viz.js"):
        assert files("thearc.flow").joinpath(asset).read_text(encoding="utf-8").strip()


def test_handoff_mode_preserves_string_contract():
    # Preserve the serialized values and public string representation.
    for mode in HandoffMode:
        assert isinstance(mode, str)
        assert str(mode) == mode.value
        assert json.loads(json.dumps({"mode": mode}))["mode"] == mode.value
        assert HandoffMode(mode.value) is mode
