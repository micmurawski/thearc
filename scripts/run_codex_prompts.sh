#!/usr/bin/env bash
# Invoke the collector from any working directory, preserving the caller's arguments.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/run_codex_prompts.py" "$@"
