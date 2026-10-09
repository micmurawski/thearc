"""Real pinned Codex runtime, local mock inference, no external model requests."""

import json
import os
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from thearc import ContextDocument, MetaAgent
from thearc.learning.reflection.providers.codex import _DISABLED_FEATURES
from thearc.learning.sessions.models import SourceConfig
from thearc.resources import ReflectionBaseModel, Reflector, SessionStore

pytestmark = pytest.mark.skipif(
    os.environ.get("THEARC_TEST_CODEX_RUNTIME") != "1", reason="Opt-in installed runtime with local mock inference"
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    sdk = pytest.importorskip("openai_codex")
    import openai_codex.client

    requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            text = (
                '{"summary":"Typed fixture result"}'
                if "TYPED_FIXTURE" in json.dumps(requests[-1])
                else "Reflection fixture result."
            )
            message = {
                "id": f"msg_{len(requests)}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
            response = {
                "id": f"resp_{len(requests)}",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "fixture-model",
                "output": [message],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "total_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }
            events = [
                {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**message, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.content_part.added",
                    "item_id": message["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": message["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "delta": text,
                },
                {
                    "type": "response.output_text.done",
                    "item_id": message["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "text": text,
                },
                {
                    "type": "response.content_part.done",
                    "item_id": message["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "part": message["content"][0],
                },
                {"type": "response.output_item.done", "output_index": 0, "item": message},
                {"type": "response.completed", "response": response},
            ]
            request = requests[-1]
            if "MCP_FIXTURE" in json.dumps(request) and not any(
                i.get("type") == "function_call_output" for i in request["input"]
            ):
                namespace = next(
                    t
                    for t in request["tools"]
                    if t.get("type") == "namespace" and t["name"].startswith("mcp__thearc_reflection_")
                )
                tool = next(t for t in namespace["tools"] if t["name"] == "submit_reflections")
                call = {
                    "id": "fc_submit",
                    "type": "function_call",
                    "call_id": "call_submit",
                    "name": tool["name"],
                    "namespace": namespace["name"],
                    "arguments": '{"reflections":[]}',
                }
                events = [
                    {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                    {"type": "response.output_item.added", "output_index": 0, "item": {**call, "arguments": ""}},
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": call["id"],
                        "output_index": 0,
                        "delta": call["arguments"],
                    },
                    {"type": "response.output_item.done", "output_index": 0, "item": call},
                    {"type": "response.completed", "response": {**response, "output": [call]}},
                ]
            body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    home = tmp_path / "runtime"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {**os.environ, "CODEX_HOME": str(home)}
    overrides = (
        'model_provider="fixture"',
        'model="fixture-model"',
        'model_providers.fixture.name="Local fixture"',
        f'model_providers.fixture.base_url="http://127.0.0.1:{server.server_port}/v1"',
        'model_providers.fixture.wire_api="responses"',
        "model_providers.fixture.requires_openai_auth=false",
        "model_providers.fixture.supports_websockets=false",
        "check_for_update_on_startup=false",
        "project_doc_max_bytes=0",
        *[f"features.{name}=false" for name in _DISABLED_FEATURES],
    )
    real_client = openai_codex.client.CodexClient

    def client(config, **kwargs):
        return real_client(replace(config, env=env, config_overrides=(*overrides, *config.config_overrides)), **kwargs)

    monkeypatch.setattr(openai_codex.client, "CodexClient", client)
    try:
        yield requests, client, sdk, workspace
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_claude_trajectory_import_is_history_not_prompt(runtime, history, tmp_path):
    requests, _, _, _ = runtime
    original = history.to_jsonl()
    reflector = Reflector(
        implementation="codex", model="fixture-model", timeout_seconds=30, artifact_dir=tmp_path / "reflections"
    )
    result = reflector.handoff("RP only", history.split_into_trajectories()[0])
    assert result.text == "Reflection fixture result."
    assert result.provenance["mode"] == "import"
    assert history.to_jsonl() == original
    assert len(requests) == 1
    items = requests[0]["input"]
    texts = [p.get("text") for item in items if item.get("type") == "message" for p in item["content"]]
    assert texts.count("RP only") == 1 and "Run tests" in texts
    assert "Summarize" not in texts and "Tests failed" not in texts
    assert any(item.get("type") == "function_call" and item["name"] == "shell" for item in items)
    assert any(item.get("type") == "function_call_output" and "SyntaxError" in str(item) for item in items)
    destination = result.execution["destination"]
    saved = Path(destination["session_path"]).read_text()
    assert "Run tests" in saved and "RP only" in saved


def test_activation_is_per_call_and_reference_mode_does_not_apply_instructions(runtime):
    requests, _, _, _ = runtime
    agent = MetaAgent(name="subject", context=[ContextDocument(filename="AGENTS.md", content="ACTIVE_SENTINEL")])
    reflector = Reflector(implementation="codex", model="fixture-model", timeout_seconds=30)
    reflector.reflect("Reflect", agent=agent, agent_mode="active")
    reflector.reflect("Reflect again", agent=agent)

    def developers(request):
        return "\n".join(str(item) for item in request["input"] if item.get("role") == "developer")

    assert "ACTIVE_SENTINEL" in developers(requests[0])
    assert "ACTIVE_SENTINEL" not in developers(requests[1])


def test_native_schema_and_typed_response(runtime):
    requests, _, _, _ = runtime

    class Report(ReflectionBaseModel):
        summary: str

    reflector = Reflector(implementation="codex", model="fixture-model", timeout_seconds=30)
    result = reflector.reflect("TYPED_FIXTURE", output_schema=Report)
    assert result.output.summary == "Typed fixture result"
    assert requests[0]["text"]["format"]["type"] == "json_schema"


def test_cookbook_native_mcp_submission(runtime, tmp_path):
    """Public Reflector -> real Codex -> real MCP; only inference is mocked."""
    pytest.importorskip("mcp")
    from cookbook.codex_example import ReflectionSchema, generate_reflections
    from thearc.resources import Session

    from .conftest import event

    requests, _, _, _ = runtime
    output = tmp_path / "prepared"
    output.mkdir()
    (output / "agent.json").write_text(MetaAgent(name="subject").model_dump_json())
    (output / "evidence.json").write_text("[]")
    (output / "reflection-schema.json").write_text(json.dumps(ReflectionSchema.model_json_schema()))
    (output / "prompt.txt").write_text("MCP_FIXTURE: submit an empty reflections list")
    Session.from_events(
        [event(0, role="user", text="Historical task"), event(1, role="assistant", text="Completed")]
    ).save(output / "history.json")
    assert generate_reflections(output, model="fixture-model", timeout=30) == []
    assert json.loads((output / "reflections.json").read_text()) == []
    assert json.loads((output / "submission.json").read_text())["accepted"] is True
    assert len(requests) == 2
    calls = json.loads(next((output / "reflection-runs").rglob("response.json")).read_text())["mcp_calls"]
    assert calls[0]["tool"] == "submit_reflections"


def test_native_prefix_remains_at_captured_boundary(runtime, tmp_path):
    requests, make_client, sdk, workspace = runtime
    client = make_client(sdk.CodexConfig(cwd=str(workspace)))
    try:
        client.start()
        client.initialize()
        started = client.thread_start(
            {
                "cwd": str(workspace),
                "model": "fixture-model",
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "ephemeral": False,
            }
        )
        sdk.Thread(client, started.thread.id).run("Original task")
        path = Path(client.thread_read(started.thread.id).thread.path)
        with SessionStore(tmp_path / "index.sqlite") as store:
            store.ingest(SourceConfig(id="fixture", harness="codex", root=path.parent))
            session_id = next(store.index.iter_sessions()).id
            captured = store.get(session_id)
            # Lazy events must stay pinned when the external source grows.
            sdk.Thread(client, started.thread.id).run("Later excluded request")
        original = path.read_bytes()
    finally:
        client.close()
    reflector = Reflector(implementation="codex", model="fixture-model", timeout_seconds=30)
    result = reflector.handoff("RP prefix", captured.through(captured.split_into_trajectories()[0]))
    assert result.provenance["mode"] == "native_fork"
    assert path.read_bytes() == original
    assert "Original task" in json.dumps(requests[-1]["input"])
    assert "Later excluded request" not in json.dumps(requests[-1]["input"])
    with SessionStore(tmp_path / "index.sqlite") as store:
        store.sync()
        grown = store.get(session_id)
    isolated = grown.split_into_trajectories()[1]
    imported = reflector.handoff("RP isolated", isolated)
    assert imported.provenance["mode"] == "import"
    assert "Later excluded request" in json.dumps(requests[-1]["input"])
    assert "Original task" not in json.dumps(requests[-1]["input"])

    # MCP is configured on the fork itself; host dynamic tools cannot be attached
    # through the pinned fork API. Preserve the exact captured boundary.
    pytest.importorskip("mcp")
    from thearc.resources import ReflectionTools

    prefix = captured.through(captured.split_into_trajectories()[0])
    tools = ReflectionTools(agent=MetaAgent(name="subject"), evidence=prefix)
    mcp_reflector = Reflector(implementation="codex", model="fixture-model", tool_transport="mcp", timeout_seconds=30)
    assert mcp_reflector.handoff("MCP_FIXTURE: submit an empty list", prefix, tools=tools) == []
    assert tools.accepted
    assert "Original task" in json.dumps(requests[-1]["input"])
    assert "Later excluded request" not in json.dumps(requests[-1]["input"])
    assert path.read_bytes() == original
