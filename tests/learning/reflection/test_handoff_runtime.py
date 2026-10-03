"""Opt-in installed-runtime acceptance using a local mock Responses endpoint."""

import json
import os
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from thearc.learning.reflection import HandoffReflectorConfig, NativeSessionRef, create_handoff_reflector
from thearc.learning.reflection.providers.codex import _DISABLED_FEATURES


@pytest.mark.skipif(os.environ.get("THEARC_TEST_CODEX_RUNTIME") != "1", reason="Opt-in installed runtime")
@pytest.mark.parametrize("trajectory_cutoff", [False, True])
def test_native_fork_persists_history_and_reflection_without_paid_inference(tmp_path, monkeypatch, trajectory_cutoff):
    sdk = pytest.importorskip("openai_codex")
    import openai_codex.client
    from openai_codex.generated.v2_all import ThreadItemsListResponse

    requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            text = "Original answer" if len(requests) == 1 else "Reflection on the original conversation."
            if trajectory_cutoff and len(requests) == 2:
                text = "Later answer"
            message = {"id": f"msg_{len(requests)}", "type": "message", "status": "completed",
                       "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]}
            response = {"id": f"resp_{len(requests)}", "object": "response", "created_at": 1,
                        "status": "completed", "model": "fixture-model", "output": [message],
                        "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20,
                                  "input_tokens_details": {"cached_tokens": 0},
                                  "output_tokens_details": {"reasoning_tokens": 0}}}
            events = [
                {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0,
                 "item": {**message, "status": "in_progress", "content": []}},
                {"type": "response.content_part.added", "item_id": message["id"], "output_index": 0,
                 "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
                {"type": "response.output_text.delta", "item_id": message["id"], "output_index": 0,
                 "content_index": 0, "delta": text},
                {"type": "response.output_text.done", "item_id": message["id"], "output_index": 0,
                 "content_index": 0, "text": text},
                {"type": "response.content_part.done", "item_id": message["id"], "output_index": 0,
                 "content_index": 0, "part": message["content"][0]},
                {"type": "response.output_item.done", "output_index": 0, "item": message},
                {"type": "response.completed", "response": response},
            ]
            body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    task_runtime_home = tmp_path / "runtime-home"
    task_runtime_home.mkdir()
    task_workspace = tmp_path / "workspace"
    task_workspace.mkdir()
    env = {**os.environ, "CODEX_HOME": str(task_runtime_home)}
    overrides = (
        'model_provider="fixture"', 'model="fixture-model"',
        'model_providers.fixture.name="Local fixture"',
        f'model_providers.fixture.base_url="http://127.0.0.1:{server.server_port}/v1"',
        'model_providers.fixture.wire_api="responses"',
        "model_providers.fixture.requires_openai_auth=false",
        "model_providers.fixture.supports_websockets=false",
        "check_for_update_on_startup=false", "project_doc_max_bytes=0",
        *[f"features.{name}=false" for name in _DISABLED_FEATURES],
    )
    real_client = openai_codex.client.CodexClient
    client = real_client(sdk.CodexConfig(cwd=str(task_workspace), env=env, config_overrides=overrides))
    try:
        client.start()
        client.initialize()
        started = client.thread_start({"cwd": str(task_workspace), "model": "fixture-model",
                                       "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": False})
        source_id = started.thread.id
        source_turn = sdk.Thread(client, source_id).run("Original task")
        assert source_turn.status.value == "completed"
        if trajectory_cutoff:
            sdk.Thread(client, source_id).run("Later user prompt")
        source_path = Path(client.thread_read(source_id).thread.path)
        client.close()
        original = source_path.read_bytes()

        def isolated_client(config, **kwargs):
            options = replace(config, env=env, config_overrides=(*overrides, *config.config_overrides))
            return real_client(options, **kwargs)

        monkeypatch.setattr(openai_codex.client, "CodexClient", isolated_client)
        reflector = create_handoff_reflector("codex", HandoffReflectorConfig(model="fixture-model", timeout_seconds=30),
                                              artifact_dir=tmp_path / "artifacts")
        source_ref = NativeSessionRef(runtime="codex", session_id=source_id)
        if trajectory_cutoff:
            from thearc.learning import SessionStore, SourceConfig

            with SessionStore(tmp_path / "index.sqlite") as store:
                store.ingest(SourceConfig(id="runtime", harness="codex", root=source_path.parent))
                session = next(store.iter_sessions())
                split = store.split_session(session.id)
                assert len(split.trajectories) == 2
                assert split.trajectories[0].native_turn_id == source_turn.id
                source_ref = NativeSessionRef.from_trajectory(session, split.trajectories[0])
        result = reflector.reflect(source_ref, prompt="RP only")
        assert result.fork.session_id != source_id
        assert result.fork.source_turn_id == source_turn.id
        assert result.reflection == "Reflection on the original conversation."
        assert source_path.read_bytes() == original
        assert Path(result.fork.session_path).is_file()
        # Native storage may share ancestor history rather than copying it into
        # the child's rollout. Read the persisted logical session through its API.
        reader = real_client(sdk.CodexConfig(cwd=str(task_workspace), env=env, config_overrides=overrides))
        try:
            reader.start()
            reader.initialize()
            items = reader.request("thread/items/list", {"threadId": result.fork.session_id, "limit": 100},
                                   response_model=ThreadItemsListResponse)
            history = items.model_dump_json()
            assert "Original task" in history and "Original answer" in history
            assert "RP only" in history and result.reflection in history
            assert "Later user prompt" not in history and "Later answer" not in history
        finally:
            reader.close()
        assert len(requests) == (3 if trajectory_cutoff else 2)
        texts = [part.get("text") for item in requests[-1]["input"] if item.get("type") == "message"
                 for part in item.get("content", []) if isinstance(part, dict)]
        assert texts.count("RP only") == 1
        assert "Original task" in texts and "Original answer" in texts
        assert "Later user prompt" not in texts and "Later answer" not in texts
        assert "UNTRUSTED EVIDENCE (JSON):" not in json.dumps(requests[-1])
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
