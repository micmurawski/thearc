import asyncio
import json
import sys

import pytest

pytest.importorskip("mcp")

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from thearc.resources import ReflectionTools
from thearc.resources.reflection_mcp import create_reflection_server, reflection_mcp_transport

from .test_reflection_tools import finding


def test_mcp_schema_errors_and_correction(agent, history, tmp_path):
    tools = ReflectionTools(agent=agent, evidence=history, output=tmp_path / "accepted.json")

    async def check():
        async with Client(create_reflection_server(tools), raise_exceptions=True) as client:
            listed = await client.list_tools()
            submit = next(tool for tool in listed.tools if tool.name == "submit_reflections")
            assert submit.input_schema == tools.definitions[-1]["inputSchema"]
            bad = finding(tools, bullet_tags=[{"id": "unknown", "tag": "harmful"}])
            response = await client.call_tool("submit_reflections", {"reflections": [bad]})
            assert response.is_error
            assert response.structured_content["errors"][0]["code"] == "unknown_resource"
            response = await client.call_tool("submit_reflections", {"reflections": [finding(tools)]})
            assert not response.is_error
            assert response.structured_content["accepted"]

    asyncio.run(check())
    assert len(tools.reflections) == 1


def test_cli_stdio_real_client_without_inference(agent, history, tmp_path):
    agent_file, evidence_file, output = (tmp_path / name for name in ("agent.json", "events.json", "submission.json"))
    agent_file.write_text(agent.model_dump_json())
    evidence_file.write_text(json.dumps([e.model_dump() for e in history.selected_events]))

    async def check():
        server = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "thearc.cli",
                "reflection",
                "serve",
                "--agent",
                str(agent_file),
                "--evidence",
                str(evidence_file),
                "--output",
                str(output),
            ],
        )
        async with Client(server, read_timeout_seconds=10) as client:
            resources = await client.call_tool(
                "list_resources",
                {
                    "query": "Retry",
                    "offset": 0,
                    "limit": 10,
                    "include_ranks": True,
                },
            )
            assert resources.structured_content["resources"][0]["target"]["section"] == "Retry"
            malformed = await client.call_tool("submit_reflections", {"reflections": [{}]})
            assert malformed.is_error
            accepted = await client.call_tool("submit_reflections", {"reflections": []})
            assert accepted.structured_content["accepted"]

    asyncio.run(check())
    assert json.loads(output.read_text())["reflections"] == []


def test_managed_transport_authentication_and_cleanup_on_error(agent, history):
    import socket
    from urllib.parse import urlparse

    import httpx2

    tools = ReflectionTools(agent=agent, evidence=history)
    with pytest.raises(RuntimeError, match="runtime failed"), reflection_mcp_transport(tools) as servers:
        settings = next(iter(servers.values()))
        address = urlparse(settings["url"])
        assert address.hostname == "127.0.0.1"
        assert httpx2.post(settings["url"], json={}).status_code == 401
        raise RuntimeError("runtime failed")
    with socket.socket() as connection:
        assert connection.connect_ex((address.hostname, address.port)) != 0
    assert not tools.accepted
