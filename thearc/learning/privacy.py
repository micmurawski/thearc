"""Shared best-effort evidence redaction and historical-rank filtering."""

import json
import re
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def redact(value: Any) -> Any:
    """Best-effort redaction; callers must still review the offline preview."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if re.search(
                r"(?i)(password|secret|api[_-]?key|authorization|access[_-]?token|refresh[_-]?token)", key
            ) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
        value = re.sub(
            r"(?i)((?:password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)[\"']?\s*[:=]\s*)"
            r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1[REDACTED]", value,
        )
        return re.sub(r"/(?:Users|home)/[^/\s\"']+", "/[USER]", value)
    return value


def hide_historical_ranks(value: Any) -> Any:
    """Hide known rank encodings, including configuration echoed by session tools.

    Arbitrary prose is not reliably classifiable as past feedback; the prompt
    also explicitly prohibits relying on historical ratings.
    """
    if isinstance(value, dict):
        return {key: hide_historical_ranks(item) for key, item in value.items() if key != "ranks"}
    if isinstance(value, list):
        return [hide_historical_ranks(item) for item in value]
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except (ValueError, TypeError):
        decoded = None
    if isinstance(decoded, (dict, list)):
        return _json(hide_historical_ranks(decoded))
    value = re.sub(
        r"\s*\((?:\s*(?:helpful|neutral|harmful)\s*:\s*\d+\s*,){2}"
        r"\s*(?:helpful|neutral|harmful)\s*:\s*\d+\s*\)", "", value,
    )
    value = re.sub(r'(?i)["\']?ranks["\']?\s*:\s*\{[^{}]*\}', "[historical ranks hidden]", value)
    return re.sub(
        r"(?m)^[ \t]*ranks:[ \t]*\n(?:[ \t]+(?:helpful|neutral|harmful):[ \t]*\d+[ \t]*\n?)+",
        "[historical ranks hidden]\n", value,
    )

