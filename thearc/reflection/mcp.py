"""Serve thearc's tools to one agent run: an MCP server inside this process, on localhost, behind a random path.

A tool is a plain function. Raising ``ValueError`` fails the call with that message,
so the agent learns what to fix and can call again.
"""

from __future__ import annotations

import functools
import secrets
import socket
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .runtimes import SERVER, ReflectionError, ToolEndpoint


@dataclass(frozen=True)
class ToolSpec:
    """One tool: its function, description, and optionally an explicit JSON Schema for its arguments."""

    function: Callable[..., str]
    description: str
    name: str = ""
    parameters: dict[str, Any] | None = None
    read_only: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@contextmanager
def serve(specs: Sequence[ToolSpec]) -> Iterator[ToolEndpoint]:
    """Serve the tools for the length of the block."""
    try:
        import uvicorn
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.server.mcpserver.tools import Tool
        from mcp.types import ToolAnnotations
    except ImportError as exc:
        raise ReflectionError("missing_runtime", "Install thearc[mcp] to run thearc's tools") from exc

    def failing_as_tool_errors(function: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(function)
        def call(*args: Any, **kwargs: Any) -> str:
            try:
                return function(*args, **kwargs)
            except ValueError as exc:
                raise ToolError(str(exc)) from None

        return call

    tools = []
    for spec in specs:
        tool = Tool.from_function(
            failing_as_tool_errors(spec.function),
            name=spec.name or spec.function.__name__,
            description=spec.description,
            annotations=ToolAnnotations(read_only_hint=spec.read_only, open_world_hint=False),
        )
        if spec.parameters is not None:
            tool.parameters = spec.parameters
        tools.append(tool)
    path = f"/{secrets.token_urlsafe(16)}/mcp"
    app = MCPServer(SERVER, tools=tools, log_level="WARNING").streamable_http_app(
        streamable_http_path=path, stateless_http=True, json_response=True
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise ReflectionError("runtime_failed", "thearc's tool server did not start")
            time.sleep(0.01)
        yield ToolEndpoint(f"http://127.0.0.1:{sock.getsockname()[1]}{path}", tuple(tool.name for tool in tools))
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
