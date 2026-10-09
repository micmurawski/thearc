"""The ``submit_reflection`` tool: how a reflector hands in its reflection, and learns what is wrong with it.

The tool is an MCP server that runs inside the reflecting process for one reflection. Its input
is the reflection schema. A call validates the reflection and checks that every ref resolves to
lines of the agent files: on any problem it fails with the reasons, located in the output, and
the reflector fixes them and calls again; on success it keeps the reflection. The reflector
stays in its own loop until the tool accepts — there are no extra turns.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from thearc.resources.files import AgentFiles
from thearc.resources.reflection import ReflectionItem, ReflectionSchema

from .mcp import ToolSpec, serve
from .runtimes import TOOL, ToolEndpoint


class Submission:
    """Checks submitted reflections; ``output`` and ``items`` hold the last accepted one (``None`` until then)."""

    def __init__(self, files: AgentFiles, schema: ReflectionSchema):
        self.files, self.schema = files, schema
        self.output: Any = None
        self.items: list[ReflectionItem] | None = None
        self.attempts = 0

    @property
    def argument(self) -> str:
        """The tool's one argument: ``items`` for an item-list schema, else ``reflection``."""
        return "items" if self.schema.is_list else "reflection"

    def submit(self, raw: Any) -> str:
        """Accept the reflection, or raise ``ValueError`` listing every problem by its location."""
        self.attempts += 1
        try:
            output, items = self.schema.check(raw, self.files)
        except ValueError as exc:
            problems = "\n".join(f"- {line}" for line in str(exc).splitlines())
            raise ValueError(f"Nothing was accepted. Fix these and submit the complete reflection again:\n{problems}")
        self.output, self.items = output, items
        return f"Accepted, with {len(items)} rating(s). The reflection is complete; end your turn."

    @contextmanager
    def serve(self) -> Iterator[ToolEndpoint]:
        """Serve ``submit_reflection`` for the length of the block; its input schema is the reflection schema."""
        output_schema = self.schema.json_schema()
        definitions = output_schema.pop("$defs", None)
        parameters = {
            "type": "object",
            "properties": {self.argument: output_schema},
            "required": [self.argument],
            **({"$defs": definitions} if definitions else {}),
        }
        if self.schema.is_list:

            def submit_reflection(items: list[Any]) -> str:
                return self.submit(items)
        else:

            def submit_reflection(reflection: dict[str, Any]) -> str:  # type: ignore[misc]
                return self.submit(reflection)

        spec = ToolSpec(
            submit_reflection,
            "Submit the reflection, complete, in one call. Fails with the reasons when anything is invalid; "
            "then fix it and submit the complete reflection again.",
            name=TOOL,
            parameters=parameters,
        )
        with serve([spec]) as endpoint:
            yield endpoint
