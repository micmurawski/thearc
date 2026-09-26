from __future__ import annotations

import inspect
import weakref
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, create_model

from . import AsyncBatchNode, AsyncNode, AsyncParallelBatchNode, BatchNode, Node
from .utils import _deep_materialize

# Sentinel: return NO_APPEND from a batch node to exclude that item from the stored results list.
NO_APPEND = object()


class _NodeDescriptor:
    """Descriptor so @node on a method gets the owning instance (owner) when exec runs."""

    def __init__(self, node_class: type, is_class_method: bool = False):
        self.node_class = node_class
        self._cache: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        if obj not in self._cache:
            self._cache[obj] = self.node_class(owner=obj)
        return self._cache[obj]


def signature_to_field_definitions(func_name: str, parameters: dict[str, inspect.Parameter]) -> dict:
    res = {}
    for name, field in parameters.items():
        if field.kind == inspect.Parameter.VAR_POSITIONAL:
            annotation = field.annotation if field.annotation is not inspect._empty else list[Any]
            res[name] = {"type": annotation, "default": []}
            continue
        if field.kind == inspect.Parameter.VAR_KEYWORD:
            annotation = field.annotation if field.annotation is not inspect._empty else dict[str, Any]
            res[name] = {"type": annotation, "default": {}}
            continue
        if field.annotation is inspect._empty:
            raise TypeError(
                f"@node function '{func_name}' parameter '{name}' has no type annotation. "
                f"All parameters must be typed, e.g. def {func_name}({name}: str)."
            )
        res[name] = {
            "type": field.annotation,
            "default": ... if field.default is inspect._empty else field.default,
        }
    return res


def signature_to_input_model(name, signature: inspect.Signature) -> dict:
    return create_model_from_dict(
        f"{name}_input_model_input", signature_to_field_definitions(name, signature.parameters)
    )


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


def _validated_kwargs(input_model: BaseModel, prep_res: dict[str, Any]) -> dict[str, Any]:
    """Validate input and ensure materialized plain types (consume one-shot Pydantic iterators)."""
    validated = input_model(**prep_res)
    raw = {name: getattr(validated, name) for name in type(validated).model_fields}
    # During injection, we want to preserve rich objects so nodes get the instances they expect.
    try:
        return _deep_materialize(raw, preserve_custom_objects=True)
    except TypeError as exc:
        model_name = getattr(input_model, "__name__", str(input_model))
        raise TypeError(
            f"Failed to materialize validated kwargs for {model_name}. Top-level keys: {list(raw.keys())}. {exc}"
        ) from exc


def __init(self, **kwargs):
    super(type(self), self).__init__()
    self.max_retries = kwargs.get("max_retries", 1)
    self.wait = kwargs.get("wait", 0)
    self.metadata = kwargs.get("metadata")


def create_prep(signature: inspect.Signature) -> Callable[[Node, Any], dict]:
    def prep_inner(self, shared: Any) -> dict:
        res = {}
        for name, field in signature.parameters.items():
            res[name] = shared.get(name)
            if res[name] is None:
                if field.kind == inspect.Parameter.VAR_KEYWORD:
                    res[name] = {}
                elif field.kind == inspect.Parameter.VAR_POSITIONAL:
                    res[name] = []
                elif field.default is not inspect._empty:
                    res[name] = field.default
                else:
                    raise ValueError(f"Parameter {name} has no default value, and is not present in shared")
        return res

    return prep_inner


def create_batch_prep(
    signature: inspect.Signature,
    input_model: BaseModel,
    items_key: str = "items",
    items_type: type[list | dict] = dict,
) -> Callable[[Any, Any], list[dict]]:
    """Prep for batch/parallel_batch nodes: reads shared[items_key] and returns a list of validated item dicts."""

    def prep_inner(self, shared: Any) -> list[dict]:
        raw_items = shared.get(items_key)
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise TypeError(
                f"For batch/parallel_batch nodes, shared[{items_key!r}]"
                f" must be a list of dicts, got {type(raw_items).__name__}"
            )
        prepped = [] if items_type is dict else {}
        for i, raw in enumerate(raw_items):
            if items_type is dict and not isinstance(raw, dict):
                raise TypeError(f"Batch item at index {i} must be a dict of arguments, got {type(raw).__name__}")
            elif items_type is not dict and not (isinstance(raw, (list, tuple))):
                raise TypeError(
                    f"Batch item at index {i} must be a list"
                    f" or tuple of arguments, got {type(raw).__name__}, raw: {raw}"
                )
            if items_type is dict:
                injected = {name: shared[name] for name in signature.parameters if name not in raw and name in shared}
                validated = input_model(**injected, **raw)
                prepped.append(
                    _deep_materialize(
                        {name: getattr(validated, name) for name in type(validated).model_fields},
                        preserve_custom_objects=True,
                    )
                )
            else:
                key, *rest = raw
                prepped[key] = rest
        return prepped

    return prep_inner


def __reduce_shared(self, shared, prep_res, exec_res):
    if isinstance(exec_res, tuple) and len(exec_res) > 1:
        state, action = exec_res
    else:
        state, action = exec_res, "default"

    if state is None:
        return action

    if not isinstance(state, dict):
        raise TypeError(f"Error at {self.__name__}: state must be a dict, got {type(state)}")
    state = _deep_materialize(state)
    shared.update(state)
    return action


def create_batch_post(
    results_key: str = "results",
    results_type: type[list | dict] = list,
):
    """
    Returns a batch node post that stores per-item results in shared[results_key].
    - results_type=list (default): store a list; each non-NO_APPEND result is appended.
    - results_type=dict: store a dict; each per-item result is (key, value) and we set shared[results_key][key] = value.
      Result can be a 2-tuple/list or a dict (merged in). NO_APPEND items are skipped.
    """

    def __reduce_shared_batch(self, shared, prep_res, exec_res):
        if not isinstance(exec_res, list):
            exec_res = []
        if results_type is dict:
            out = {}
            for r in exec_res:
                if r is NO_APPEND:
                    continue
                if isinstance(r, (tuple, list)) and len(r) >= 2:
                    out[r[0]] = r[1]
                elif isinstance(r, dict):
                    out.update(r)
                else:
                    raise TypeError(
                        "For results_type=dict, each batch item must return "
                        f"(key, value) or a dict, got {type(r).__name__}"
                    )
            shared[results_key] = out
        else:
            out = [r for r in exec_res if r is not NO_APPEND]
            shared[results_key] = out
        return "default"

    return __reduce_shared_batch


def node(
    func=None,
    *,
    max_retries=1,
    wait=0,
    batch: bool = False,
    parallel_batch: bool = False,
    items_key: str = "items",
    results_key: str = "results",
    results_type: type[list | dict] = list,
    metadata: dict[str, Any] | None = None,
):
    """
    Decorator that creates a PocketFlow Node instance from a function.

    Can be used as:
    - @node (uses default args)
    - @node() (uses default args)
    - @node(max_retries=3, wait=2) (custom args)
    - @node(batch=True) or @node(parallel_batch=True) for batch nodes (reads shared[items_key])

    Args:
        func: The function to decorate (when used as @node)
        max_retries (int): Maximum number of retries for the node (default: 1)
        wait (int): Wait time in seconds between retries (default: 0)
        batch (bool): If True, use BatchNode/AsyncBatchNode; prep reads shared[items_key] as list of item dicts.
        parallel_batch (bool): If True, use AsyncParallelBatchNode; same prep contract as batch.
        items_key (str): Key in shared for the list of batch items when batch or parallel_batch is True
        (default: "items").
        results_key (str): Key in shared where batch results are stored (default: "results").
        results_type (list | dict): If list (default), store a list of per-item results.
        If dict, each item should return (key, value) and results are merged into shared[results_key] as a dict.

    Returns:
        A Node instance with the function name as the class name
    """

    def decorator(func):
        class_name = func.__name__
        signature = inspect.signature(func)

        # Determine if 'self' is the first parameter (i.e., if the func is a method)
        parameters = list(signature.parameters.values())

        is_method = len(parameters) > 0 and parameters[0].name == "self"
        is_class_method = len(parameters) > 0 and parameters[0].name == "cls"
        is_async = inspect.iscoroutinefunction(func)

        if is_method or is_class_method:
            parameters = parameters[1:]
            signature = inspect.Signature(parameters)
        input_model = signature_to_input_model(class_name, signature)
        is_method_or_cls = is_method or is_class_method

        def make_node_init(owner_param: bool):
            def node_init(self, owner=None, **kwargs):
                if owner_param:
                    self.owner = owner
                __init(self, max_retries=max_retries, wait=wait, metadata=metadata, **kwargs)

            return node_init

        if not is_async:
            node_class = Node if not batch else BatchNode
            is_batch = node_class is BatchNode
            items_type = dict if results_type is list else list
            prep_fn = (
                create_batch_prep(signature, input_model, items_key, items_type) if is_batch else create_prep(signature)
            )
            post_fn = create_batch_post(results_key, results_type) if is_batch else __reduce_shared
            if is_method_or_cls:

                def exec_sync(self, prep_res):
                    return func(self.owner, **_validated_kwargs(input_model, prep_res))

                node_class = type(
                    class_name,
                    (node_class,),
                    {
                        "__init__": make_node_init(True),
                        "exec": exec_sync,
                        "prep": prep_fn,
                        "post": post_fn,
                        "__doc__": func.__doc__,
                        "__module__": func.__module__,
                        "__name__": func.__name__,
                    },
                )
                return _NodeDescriptor(node_class)
            node_class = type(
                class_name,
                (node_class,),
                {
                    "__init__": make_node_init(False),
                    "exec": lambda self, prep_res: func(**_validated_kwargs(input_model, prep_res)),
                    "prep": prep_fn,
                    "post": post_fn,
                    "__doc__": func.__doc__,
                    "__module__": func.__module__,
                    "__name__": func.__name__,
                },
            )
            return node_class()
        else:
            node_class = AsyncNode
            if parallel_batch:
                node_class = AsyncParallelBatchNode
            elif batch:
                node_class = AsyncBatchNode
            is_batch = node_class in (AsyncBatchNode, AsyncParallelBatchNode)
            prep_fn = create_batch_prep(signature, input_model, items_key) if is_batch else create_prep(signature)
            post_fn = create_batch_post(results_key, results_type) if is_batch else __reduce_shared

            async def exec_async(self, prep_res):
                kwargs = _validated_kwargs(input_model, prep_res)
                if is_method_or_cls:
                    return await func(self.owner, **kwargs)
                return await func(**kwargs)

            async def post(self, shared, prep_res, exec_res):
                return post_fn(self, shared, prep_res, exec_res)

            async def prep(self, shared):
                return prep_fn(self, shared)

            if is_method_or_cls:
                node_class = type(
                    class_name,
                    (node_class,),
                    {
                        "__init__": make_node_init(True),
                        "exec_async": exec_async,
                        "prep_async": prep,
                        "post_async": post,
                        "__doc__": func.__doc__,
                        "__module__": func.__module__,
                        "__name__": func.__name__,
                    },
                )
                return _NodeDescriptor(node_class)
            node_class = type(
                class_name,
                (node_class,),
                {
                    "__init__": make_node_init(False),
                    "exec_async": exec_async,
                    "prep_async": prep,
                    "post_async": post,
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


@node
async def end(messages: list[dict]):
    return {"messages": messages}


@node
def noop(messages: list[dict]):
    return {"messages": messages}
