#!/usr/bin/env bash
# Lint and type-check thearcv2: ruff (style, imports, common bugs) and mypy (types, settings in pyproject.toml).
# Usage: ./lint.sh [--fix]   (--fix lets ruff apply safe fixes before checking)
set -euo pipefail

cd "$(dirname "$0")"
bin=".venv/bin"
[[ -x "$bin/ruff" && -x "$bin/mypy" ]] || { echo "Install dev tools first: pip install -e '.[dev]'" >&2; exit 1; }

status=0
if [[ "${1:-}" == "--fix" ]]; then
    "$bin/ruff" check thearcv2 --fix --quiet || true
fi
echo "== ruff"
"$bin/ruff" check thearcv2 || status=1
echo "== mypy"
"$bin/mypy" || status=1
exit "$status"
