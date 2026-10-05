#!/usr/bin/env python3
"""Codex entry point for the shared prompt collector."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_agent_prompts import extract_prompt, find_session, git, inspect_stream  # noqa: F401
from scripts.run_agent_prompts import main as collect_main


def main(argv=None):
    return collect_main(argv, default_harness="codex")


if __name__ == "__main__":
    sys.exit(main())
