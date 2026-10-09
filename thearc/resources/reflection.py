"""Reflection schema: the operator's contract for reflection output, and how ratings are found in it.

A reflection schema is any Pydantic model (or type, such as a list of models). thearc looks for
two kinds of fields, by type and never by name:

- ``Ref`` — a file reference, ``path#Lx`` or ``path#Lx-Ly``. Wherever it appears (alone, in a
  list, nested) it must resolve to lines of the agent files. On its own it only cites.
- ``Annotated[str, scale]`` (or ``scale.field()``) — a rating on that scale. An object holding a
  rating must also hold exactly one required ``Ref``: the lines the rating is about. That rule is
  checked when the schema is built.

    class Rated(BaseModel):
        ref: Ref
        usefulness: Annotated[str, usefulness]

    class Lesson(BaseModel):
        key_insight: str
        ratings: list[Rated]

    schema = ReflectionSchema(Lesson)

Every object holding a rating yields a ``ReflectionItem`` per rating field: its ref, value,
scale, location in the output, and the object's other fields. Everything else stays in the
reflection's output. ``ReflectionSchema(scale, Metadata)`` is shorthand for a list of
``{ref, rating, …Metadata}`` items.
"""

from __future__ import annotations

import copy
import types
import typing
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, create_model

from thearc.resources.agent import MetaAgent
from thearc.resources.files import AgentFiles, FileReference
from thearc.resources.journal import Rated, derived_entry_id
from thearc.resources.ratings import RatingScale, RatingValue

if TYPE_CHECKING:
    from thearc.reflection import Reflection

REFERENCE_PATTERN = r"^[^#\s]+#L[1-9][0-9]*(-L[1-9][0-9]*)?$"


class _RefField:
    """Marks a field of a reflection schema as a file reference."""


Ref = Annotated[
    str,
    _RefField(),
    Field(pattern=REFERENCE_PATTERN, description="Agent file lines: path#Lx or path#Lx-Ly (1-based, inclusive)."),
]


@dataclass(frozen=True)
class ReflectionItem:
    """One rating found in a reflection: the lines it is about, its value and scale, where it sits in the
    output (a JSON pointer to the object holding it), and that object's other fields."""

    ref: FileReference
    rating: RatingValue
    metadata: dict[str, Any]
    scale: str = ""
    path: str = ""


@dataclass(frozen=True)
class Rejection:
    index: int
    item: Any
    reason: str


@dataclass(frozen=True)
class ReflectionResult:
    """Rated entries to record, and the items that were rejected instead."""

    entries: tuple[Rated, ...]
    rejected: tuple[Rejection, ...]


# --- reading a schema's types ---------------------------------------------------------------------------------------


def _marker(metadata: Iterable[Any]) -> _RefField | RatingScale | None:
    return next((m for m in metadata if isinstance(m, _RefField | RatingScale)), None)


def _role(annotation: Any, metadata: Iterable[Any] = ()) -> tuple[str, Any]:
    """``("ref" | "refs" | "rating" | "other", scale or None)`` for a field's type."""
    marker = _marker(metadata)
    if isinstance(marker, _RefField):
        return "ref", None
    if isinstance(marker, RatingScale):
        return "rating", marker
    if get_origin(annotation) is Annotated:
        base, *extra = get_args(annotation)
        return _role(base, extra)
    if get_origin(annotation) in {typing.Union, types.UnionType}:
        roles = [_role(arg) for arg in get_args(annotation) if arg is not type(None)]
        return roles[0] if len(roles) == 1 else ("other", None)
    if any(_role(arg)[0] in {"ref", "refs"} for arg in get_args(annotation)):
        return "refs", None
    return "other", None


def _models(annotation: Any) -> Iterator[type[BaseModel]]:
    """Every model reachable from a type (through lists, unions, dicts, and fields)."""
    seen: set[type] = set()
    pending = [annotation]
    while pending:
        current = pending.pop()
        if isinstance(current, type) and issubclass(current, BaseModel):
            if current in seen:
                continue
            seen.add(current)
            yield current
            pending += [info.annotation for info in current.model_fields.values()]
        else:
            pending += list(get_args(current))


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """The JSON Schema with local ``$ref``s replaced by their definitions (unless recursive), for models to read."""
    definitions = schema.get("$defs", {})
    recursive = False

    def resolve(node: Any, trail: tuple[str, ...]) -> Any:
        nonlocal recursive
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name = reference.removeprefix("#/$defs/")
                if name in trail:
                    recursive = True
                    return node
                rest = {key: value for key, value in node.items() if key != "$ref"}
                return {**resolve(copy.deepcopy(definitions[name]), (*trail, name)), **resolve(rest, trail)}
            return {key: resolve(value, trail) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(value, trail) for value in node]
        return node

    result = resolve(schema, ())
    return {**result, "$defs": definitions} if recursive else result


def pointer(parts: Iterable[Any]) -> str:
    """A JSON pointer, e.g. ``/ratings/2``."""
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


class ReflectionSchema:
    """Validates reflection output and finds the ratings in it (see the module docstring)."""

    def __init__(self, output: Any, metadata: type[BaseModel] | None = None):
        if isinstance(output, RatingScale):
            output = list[_item_model(output, metadata)]  # type: ignore[misc]
        elif metadata is not None:
            raise TypeError("metadata is only for ReflectionSchema(scale, metadata)")
        self.output = output
        self.adapter: TypeAdapter[Any] = TypeAdapter(output)
        self.scales: dict[str, RatingScale] = {}
        self._roles: dict[type[BaseModel], dict[str, tuple[str, Any]]] = {}
        for model in _models(output):
            roles = {name: _role(info.annotation, info.metadata) for name, info in model.model_fields.items()}
            self._roles[model] = roles
            ratings = [name for name, (role, _) in roles.items() if role == "rating"]
            refs = [name for name, (role, _) in roles.items() if role == "ref"]
            if ratings and (len(refs) != 1 or not model.model_fields[refs[0]].is_required()):
                raise ValueError(
                    f"{model.__name__}: a rating needs exactly one required Ref next to it "
                    f"(ratings {ratings}, refs {refs})"
                )
            for name in ratings:
                scale = roles[name][1]
                if self.scales.setdefault(scale.name, scale) is not scale:
                    raise ValueError(f"two different scales are named {scale.name!r}")

    @property
    def scale(self) -> RatingScale:
        """The schema's only scale."""
        if len(self.scales) != 1:
            raise ValueError(f"the schema has {len(self.scales)} scales: {sorted(self.scales)}")
        return next(iter(self.scales.values()))

    @property
    def is_list(self) -> bool:
        return get_origin(self.output) in {list, tuple}

    def json_schema(self) -> dict[str, Any]:
        """JSON Schema of the reflection output, with definitions inlined, e.g. for a tool or a prompt."""
        return _inline(self.adapter.json_schema())

    def validate(self, raw: Any) -> tuple[Any, list[ReflectionItem], list[tuple[str, FileReference]]]:
        """The output as JSON, its ratings, and every ref with its location; ``ValueError`` lists every problem."""
        try:
            value = self.adapter.validate_python(raw)
        except ValidationError as error:
            raise ValueError("\n".join(f"{pointer(e['loc']) or '/'}: {e['msg']}" for e in error.errors())) from None
        items: list[ReflectionItem] = []
        refs: list[tuple[str, FileReference]] = []
        self._walk(value, (), items, refs)
        return self.adapter.dump_python(value, mode="json"), items, refs

    def check(self, raw: Any, files: AgentFiles) -> tuple[Any, list[ReflectionItem]]:
        """``validate``, and every ref must resolve to lines of ``files``."""
        output, items, refs = self.validate(raw)
        errors = []
        for where, ref in refs:
            try:
                files.resolve(ref)
            except ValueError as error:
                errors.append(f"{where}: {error}")
        if errors:
            raise ValueError("\n".join(errors))
        return output, items

    def _walk(
        self, value: Any, at: tuple[Any, ...], items: list[ReflectionItem], refs: list[tuple[str, FileReference]]
    ) -> None:
        if isinstance(value, BaseModel):
            roles = self._roles.get(type(value), {})
            ref = None
            ratings = []
            for name, (role, scale) in roles.items():
                field = getattr(value, name)
                if role == "ref" and field is not None:
                    ref = FileReference.parse(field)
                    refs.append((pointer((*at, name)), ref))
                elif role == "refs":
                    self._refs(field, (*at, name), refs)
                elif role == "rating":
                    if field is not None:
                        ratings.append((scale, field))
                else:
                    self._walk(field, (*at, name), items, refs)
            if ratings and ref is not None:
                skip = {name for name, (role, _) in roles.items() if role in {"ref", "rating"}}
                metadata = value.model_dump(mode="json", include=set(roles) - skip)
                metadata.update(value.model_extra or {})
                items += [ReflectionItem(ref, rating, metadata, scale.name, pointer(at)) for scale, rating in ratings]
        elif isinstance(value, list | tuple):
            for index, element in enumerate(value):
                self._walk(element, (*at, index), items, refs)
        elif isinstance(value, dict):
            for key, element in value.items():
                self._walk(element, (*at, key), items, refs)

    def _refs(self, value: Any, at: tuple[Any, ...], refs: list[tuple[str, FileReference]]) -> None:
        if isinstance(value, str):
            refs.append((pointer(at), FileReference.parse(value)))
        elif isinstance(value, list | tuple):
            for index, element in enumerate(value):
                self._refs(element, (*at, index), refs)

    # --- the item-list shorthand -------------------------------------------------------------------------------------

    def validate_item(self, raw: Any) -> ReflectionItem:
        """One element of an item-list schema."""
        if not self.is_list:
            raise TypeError("validate_item needs an item-list schema, e.g. ReflectionSchema(scale, metadata)")
        _, items, _ = self.validate([raw])
        if len(items) != 1:
            raise ValueError("an item must hold exactly one rating")
        return items[0]

    def record(
        self, items: Iterable[Any], agent: MetaAgent, *, at: datetime | None = None, **extra: Any
    ) -> ReflectionResult:
        """Validate an item list against ``agent`` (the revision that was judged) and build Rated entries.

        Each valid item yields one entry per target its reference resolves to; those entries share an
        ``item_id``. Invalid items are rejected, the rest recorded. Keyword arguments become ``extra``.
        """
        at = at or datetime.now(UTC)
        files = agent.files()
        entries: list[Rated] = []
        rejected: list[Rejection] = []
        for index, raw in enumerate(items):
            try:
                item = self.validate_item(raw)
                targets = files.resolve(item.ref)
            except ValueError as error:
                rejected.append(Rejection(index, raw, str(error)))
                continue
            item_id = "item_" + uuid.uuid4().hex
            entries += [
                Rated(
                    target=target,
                    revision=agent.revision,
                    at=at,
                    extra=extra,
                    scale=item.scale,
                    value=item.rating,
                    ref=str(item.ref),
                    item_id=item_id,
                    metadata=item.metadata,
                )
                for target in targets
            ]
        return ReflectionResult(tuple(entries), tuple(rejected))


def _item_model(scale: RatingScale, metadata: type[BaseModel] | None) -> type[BaseModel]:
    """``{ref, rating, …metadata}``: the item of ``ReflectionSchema(scale, metadata)``."""
    fields: dict[str, Any] = {"ref": (Ref, ...), "rating": (scale.field(), ...)}
    if metadata is None:  # Any further JSON fields are kept as they are.
        return create_model("Item", __config__=ConfigDict(extra="allow"), **fields)
    if clash := {"ref", "rating"} & set(metadata.model_fields):
        raise ValueError(f"metadata fields cannot be named {sorted(clash)}")
    fields |= {name: (info.annotation, info) for name, info in metadata.model_fields.items()}
    return create_model(
        metadata.__name__, __config__=ConfigDict(extra="forbid"), __doc__=metadata.__doc__, **fields
    )


def reflection_entries(reflection: Reflection, files: AgentFiles) -> list[Rated]:
    """A reflection's Rated entries: each item once per target its reference resolves to in ``files``.

    ``files`` must be the judged revision. IDs derive from the reflection, the item's location, its
    scale and the target, so the same reflection always yields the same entries.
    """
    extra = {
        "reflection": reflection.id,
        "reflector": reflection.reflector.get("harness"),
        **({"model": reflection.reflector["model"]} if reflection.reflector.get("model") else {}),
        **reflection.extra,
    }
    entries = []
    for item in reflection.items:
        item_id = reflection.item_id(item)
        entries += [
            Rated(
                id=derived_entry_id(reflection.at, item_id, item.scale, str(target)),
                target=target,
                revision=reflection.revision,
                at=reflection.at,
                extra=extra,
                scale=item.scale,
                value=item.rating,
                ref=str(item.ref),
                item_id=item_id,
                metadata=item.metadata,
            )
            for target in files.resolve(item.ref)
        ]
    return entries
