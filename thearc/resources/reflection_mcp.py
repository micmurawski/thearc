"""Managed HTTP and standalone stdio MCP transports for ReflectionTools."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager


@contextmanager
def reflection_mcp_transport(tools):
    """Expose this job's tools over loopback MCP for the lifetime of one run.

    Keep the original Python schema and rank ledger in the owning process so
    custom validators run inside submit_reflections, including correction retries.
    """
    import secrets
    import socket
    import time
    from contextlib import asynccontextmanager
    from threading import Thread
    from uuid import uuid4

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Route

    manager = StreamableHTTPSessionManager(create_reflection_server(tools), stateless=True, json_response=True)
    token = secrets.token_urlsafe(32)

    async def endpoint(scope, receive, send):
        headers = dict(scope.get("headers", []))
        if not secrets.compare_digest(headers.get(b"authorization", b""), f"Bearer {token}".encode()):
            await Response(status_code=401)(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    # An ASGI object avoids Starlette treating a function as a Request endpoint.
    class Endpoint:
        async def __call__(self, scope, receive, send):
            await endpoint(scope, receive, send)

    app = Starlette(routes=[Route("/mcp", Endpoint())], lifespan=lifespan)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False,
                                              timeout_graceful_shutdown=2))
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not worker.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Reflection MCP server did not start")
                time.sleep(0.01)
            names = [definition["name"] for definition in tools.definitions]
            yield {"thearc_reflection_" + uuid4().hex[:12]: {
                "url": f"http://127.0.0.1:{listener.getsockname()[1]}/mcp",
                "http_headers": {"Authorization": f"Bearer {token}"},
                "enabled": True, "required": True, "startup_timeout_sec": 30,
                "enabled_tools": names,
                "tools": {name: {"approval_mode": "approve"} for name in names},
            }}
        finally:
            server.should_exit = True
            worker.join(timeout=5)


def create_reflection_server(tools):
    """Create a server for one reflection job, owned by its host application."""
    try:
        from mcp.server.lowlevel import Server
        from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
    except ImportError as exc:
        raise ImportError("Install thearc[mcp] to serve reflection tools") from exc

    async def list_tools(context, params):
        return ListToolsResult(tools=[Tool(name=item["name"], description=item["description"],
                                           input_schema=item["inputSchema"]) for item in tools.definitions])

    async def call_tool(context, params):
        try:
            result = tools.call(params.name, params.arguments or {})
        except OSError:
            result = {"accepted": False, "errors": [{"path": "", "code": "storage_error",
                       "message": "Could not save the submission; no batch was accepted. Check server storage."}]}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                              structured_content=result, is_error=result.get("accepted") is False)

    return Server("thearc-reflection", instructions=tools.instructions,
                  on_list_tools=list_tools, on_call_tool=call_tool)


def serve_reflections(tools):
    """Run one job over stdio. stdout is reserved for MCP messages."""
    server = create_reflection_server(tools)

    async def run():
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    try:
        asyncio.run(run())
    finally:
        tools.close()
