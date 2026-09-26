from __future__ import annotations

import inspect
import json
import os
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, create_model

from . import Node

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
        "timestamp": datetime.now(timezone.utc).isoformat(),
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


def signature_to_field_definitions(parameters: dict[str, inspect.Parameter]) -> dict:
    res = {}
    for name, field in parameters.items():
        res[name] = {
            "type": field.annotation,
            "default": ... if field.default is inspect._empty else field.default,
        }
    return res


def signature_to_input_model(name, signature: inspect.Signature) -> dict:
    return create_model_from_dict(f"{name}_input", signature_to_field_definitions(signature.parameters))


def create_model_from_dict(model_name: str, field_definitions: dict):
    """Create a Pydantic model from field definitions"""
    fields = {}

    for field_name, field_config in field_definitions.items():
        field_type = field_config["type"]
        default = field_config.get("default", ...)
        description = field_config.get("description", "")
        if description:
            fields[field_name] = (field_type, Field(default=default, description=description))
        else:
            fields[field_name] = (field_type, default)
    return create_model(model_name, **fields)


def __init(self, **kwargs: dict[str, Any]):
    super(type(self), self).__init__()
    self.max_retries = kwargs.get("max_retries", 1)
    self.wait = kwargs.get("wait", 0)


def create_prep(signature: inspect.Signature) -> Callable[[Node, Any], dict]:
    def prep_inner(self, shared: Any) -> dict:
        res = {}
        for name, field in signature.parameters.items():
            res[name] = shared.get(name)
            if field.default is inspect._empty and res[name] is None:
                raise ValueError(f"Parameter {name} has no default value")
            elif res[name] is None:
                res[name] = field.default
        return res

    return prep_inner


def shallow_deserialize(model: BaseModel) -> dict:
    res = {}
    for k in type(model).model_fields:
        res[k] = getattr(model, k)
    return res


def __reduce_shared(self, shared, prep_res, exec_res) -> str:
    if isinstance(exec_res, tuple) and len(exec_res) > 1:
        state, action = exec_res
    else:
        state, action = exec_res, "default"

    if state is None:
        return action

    if not isinstance(state, dict):
        raise TypeError(f"Error at {self.__name__}: state must be a dict, got {type(state)}")
    # Never store non-JSON-serializable values (e.g. SerializationIterator, Pydantic models) in shared.
    state = _deep_materialize(state)
    shared.update(state)
    return action


def node(func=None, *, max_retries=1, wait=0):
    """
    Decorator that creates a PocketFlow Node instance from a function.

    Can be used as:
    - @node (uses default args)
    - @node() (uses default args)
    - @node(max_retries=3, wait=2) (custom args)

    Args:
        func: The function to decorate (when used as @node)
        max_retries (int): Maximum number of retries for the node (default: 1)
        wait (int): Wait time in seconds between retries (default: 0)

    Returns:
        A Node instance with the function name as the class name
    """

    def decorator(func):
        class_name = func.__name__
        signature = inspect.signature(func)

        node_class = type(
            class_name,
            (Node,),
            {
                "__init__": lambda self, **kwargs: __init(self, max_retries=max_retries, wait=wait, **kwargs),
                "_input_model": signature_to_input_model(f"{class_name}_input_model", signature),
                "exec": lambda self, prep_res: func(
                    **_deep_materialize(shallow_deserialize(self._input_model(**prep_res)))
                ),
                "prep": create_prep(signature),
                "post": __reduce_shared,
                "__doc__": func.__doc__,
                "__module__": func.__module__,
                "__name__": func.__name__,
            },
        )

        return node_class()

    if func is None:
        return decorator
    else:
        return decorator(func)
