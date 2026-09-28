"""Packaging declarations and compatibility contracts independent of optional SDKs."""

import json
from importlib.resources import files
from pathlib import Path

from thearc.config import tomllib
from thearc.learning.runtime.handoff import HandoffMode


def test_visualization_assets_declared_and_present():
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(project.read_text())
    assert set(config["tool"]["setuptools"]["package-data"]["thearc.flow"]) == {"viz.css", "viz.js"}
    for asset in ("viz.css", "viz.js"):
        assert files("thearc.flow").joinpath(asset).read_text(encoding="utf-8").strip()


def test_handoff_mode_preserves_string_contract():
    # Use str + Enum so core imports also work on Python 3.10 (no StrEnum).
    for mode in HandoffMode:
        assert isinstance(mode, str)
        assert str(mode) == mode.value
        assert json.loads(json.dumps({"mode": mode}))["mode"] == mode.value
        assert HandoffMode(mode.value) is mode
