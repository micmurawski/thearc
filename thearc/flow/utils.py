from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# JSON-serializable primitives; anything else (dict/list we recurse; iterators/models we materialize).
_JSON_PRIMITIVES = (type(None), str, int, float, bool)


def _safe_repr(value: Any, max_len: int = 500) -> str:
    try:
        out = repr(value)
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensive
        out = f"<repr_failed: {type(exc).__name__}: {exc}>"
    return out if len(out) <= max_len else out[:max_len] + "...<truncated>"


def _path_to_str(path: tuple[Any, ...]) -> str:
    if not path:
        return "<root>"
    parts: list[str] = []
    for segment in path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(str(segment) if not parts else f".{segment}")
    return "".join(parts)


def _write_materialize_error_log(
    path: tuple[Any, ...],
    obj: Any,
    preserve_custom_objects: bool,
    error: Exception,
) -> None:
    log_path = os.environ.get("FRAMEWORK_MATERIALIZE_ERROR_LOG", "logs/materialize_errors.jsonl")
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "path": _path_to_str(path),
        "type": type(obj).__name__,
        "preserve_custom_objects": preserve_custom_objects,
        "error_type": type(error).__name__,
        "error": str(error),
        "preview": _safe_repr(obj),
    }
    # Diagnostics must never break execution paths.
    with suppress(OSError):
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _deep_materialize(obj: Any, preserve_custom_objects: bool = False, _path: tuple[Any, ...] = ()) -> Any:
    """
    Recursively convert to JSON-serializable plain dict/list/primitive.
    Consumes one-shot iterators (e.g. Pydantic SerializationIterator) and
    converts Pydantic models via model_dump().

    Args:
        obj: The object to materialize.
        preserve_custom_objects: If True, objects with model_dump() (like Task)
            are preserved as-is. Useful when injecting into nodes so they
            receive rich objects. If False (default), they are materialized.
    """
    if obj is None or isinstance(obj, _JSON_PRIMITIVES):
        return obj
    if isinstance(obj, os.PathLike):
        return os.fspath(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value

    # Preserve rich dependency objects (for example the visualizer logger)
    # while still recursively materializing ordinary containers.
    if preserve_custom_objects and not isinstance(obj, (dict, list, tuple, set)) and not hasattr(obj, "__next__"):
        return obj

    if hasattr(obj, "model_dump"):
        return _deep_materialize(obj.model_dump(), preserve_custom_objects=preserve_custom_objects, _path=_path)
    if isinstance(obj, dict):
        return {
            k: _deep_materialize(v, preserve_custom_objects=preserve_custom_objects, _path=(*_path, k))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [
            _deep_materialize(x, preserve_custom_objects=preserve_custom_objects, _path=(*_path, i))
            for i, x in enumerate(obj)
        ]

    # Handle other iterables (e.g., Pydantic's ValidatorIterator/SerializationIterator).
    # If iteration itself fails (e.g. a Pydantic ValidationError triggered by lazily-
    # validated Iterable[...] fields such as Anthropic MessageParam.content hitting an
    # unknown block type), re-raise with context instead of masking as a misleading
    # "not JSON-serializable" TypeError.
    if hasattr(obj, "__iter__"):
        try:
            return [
                _deep_materialize(x, preserve_custom_objects=preserve_custom_objects, _path=(*_path, i))
                for i, x in enumerate(obj)
            ]
        except TypeError:
            pass
        except ValueError as e:
            path_s = _path_to_str(_path)
            wrapped = TypeError(f"Failed to materialize iterable at '{path_s}' of type {type(obj).__name__}: {e}")
            _write_materialize_error_log(_path, obj, preserve_custom_objects, wrapped)
            raise wrapped from e

    path_s = _path_to_str(_path)
    error = TypeError(
        f"Object at '{path_s}' of type {type(obj).__name__} is not JSON-serializable and cannot be materialized."
    )
    _write_materialize_error_log(_path, obj, preserve_custom_objects, error)
    raise error
