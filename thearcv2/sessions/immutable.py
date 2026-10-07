"""Immutable JSON-backed values at the public resource boundary."""

from collections.abc import Mapping
from types import MappingProxyType


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


class Record:
    """Read-only attribute access with an explicitly detached JSON representation."""

    __slots__ = ("_data",)

    def __init__(self, data):
        object.__setattr__(self, "_data", freeze(data))

    def __setattr__(self, name, value):
        raise AttributeError("Historical records are immutable")

    def __getattr__(self, name):
        try:
            value = self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
        return Record(value) if name == "reference" else value

    def model_dump(self, **kwargs):
        return thaw(self._data)

    def __repr__(self):
        return f"Record(id={self._data.get('id')!r})"
