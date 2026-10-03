"""Stable fixture paths independent of test module nesting."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FASTAPI_PROJECT = REPOSITORY_ROOT / "tests" / "fastapi"
