import asyncio
import json

import pytest

from cookbook import codex_example
from thearc import MetaAgent, Skill
from thearc.models import ResourceTarget
from thearc.resources import ReflectionError


@pytest.fixture
def claude_batch(tmp_path):
    batch = tmp_path / "logs"
    (batch / "sessions").mkdir(parents=True)
    agent = MetaAgent(name="recorded-claude", skills=[Skill(name="debug", instructions="# Retry\nInspect errors")])
    for run_id in ("a", "b"):
        run = batch / "runs" / run_id
        run.mkdir(parents=True)
        (run / "agent-before.json").write_text(agent.model_dump_json())
        (run / "manifest.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "harness": "claude",
                    "thread_id": run_id,
                    "prompt": run_id,
                    "transcript": f"/old/machine/sessions/{run_id}.jsonl",
                }
            )
        )
        records = [
            {"type": "user", "sessionId": run_id, "message": {"role": "user", "content": "Run the tests"}},
            {"type": "attachment", "sessionId": run_id, "attachment": {"type": "model", "text": "native state"}},
            {
                "type": "assistant",
                "sessionId": run_id,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "pytest --bad"}},
                    ],
                },
            },
            {
                "type": "user",
                "sessionId": run_id,
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call", "content": "Invalid argument", "is_error": True},
                    ],
                },
            },
            {"type": "assistant", "sessionId": run_id, "message": {"role": "assistant", "content": "Tests failed"}},
            {"type": "user", "sessionId": run_id, "message": {"role": "user", "content": "EXCLUDED SECOND TASK"}},
            {"type": "assistant", "sessionId": run_id, "message": {"role": "assistant", "content": "Other result"}},
        ]
        (batch / "sessions" / f"{run_id}.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    return batch


def test_load_resolves_batch_session_and_recorded_agent(claude_batch, tmp_path):
    history, agent = codex_example.load(claude_batch, "b", index=tmp_path / "index.sqlite")
    assert agent.name == "recorded-claude"
    assert history.session.metadata.native_id == "b"
    export = history.to_codex()
    items = [json.loads(line)["payload"] for line in export.text.splitlines()]
    assert [i["type"] for i in items] == ["message", "function_call", "function_call_output", "message"]
    assert items[1]["call_id"] == items[2]["call_id"]
    assert json.loads(items[2]["output"])["status"] == "failure"
    assert "EXCLUDED SECOND TASK" not in export.text and "native state" not in export.text
    assert export.transformations[0].startswith("1 metadata record(s)")
    assert any(e.raw.get("type") == "attachment" for e in history.selected_events)


def test_generation_uses_real_mcp_and_validates_corrections(claude_batch, tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from pydantic import field_validator

    class CustomReflection(codex_example.ReflectionSchema):
        @field_validator("key_insight")
        @classmethod
        def check_insight(cls, value):
            if value == "invalid":
                raise ValueError("Give a concrete lesson")
            return value

    monkeypatch.setattr(codex_example, "ReflectionSchema", CustomReflection)

    output = tmp_path / "prepared"
    history, agent = codex_example.load(claude_batch, "a", index=tmp_path / "index.sqlite")

    def model(prompt, schema, config, **options):
        assert "Invalid argument" not in prompt and schema is None
        assert len(options["history_items"]) == 4
        assert options["scoped_tools"] is None
        options["on_import"]({"session_id": "new-codex-session", "event_count": 4})
        servers = options["mcp_servers"]
        server_name, settings = next(iter(servers.items()))

        async def check():
            async with httpx2.AsyncClient(headers=settings["http_headers"]) as http:
                transport = streamable_http_client(settings["url"], http_client=http)
                async with Client(transport) as client:
                    await submit(client)

        async def submit(client):
            listed = await client.call_tool("list_files", {})
            assert {"path": "skills/debug/SKILL.md", "lines": 3} in listed.structured_content["files"]
            page = await client.call_tool(
                "read_file", {"path": "skills/debug/SKILL.md", "start_line": 1, "end_line": 3}
            )
            assert page.structured_content["content"].startswith("1 | # Retry")
            finding = {
                "reasoning": "The command used an invalid flag",
                "error_identification": "Invalid flag",
                "root_cause_analysis": "Arguments were not checked",
                "correct_approach": "Check tool usage",
                "key_insight": "Validate command flags",
                "limitations": [],
                "resource_refs": [{"ref": "skills/debug/SKILL.md#L9", "tag": "harmful"}],
            }
            bad = await client.call_tool("submit_reflections", {"reflections": [finding]})
            assert bad.is_error and "3 lines" in str(bad.structured_content)
            finding["resource_refs"][0]["ref"] = "skills/debug/SKILL.md#L1-L3"
            finding["key_insight"] = "invalid"
            bad = await client.call_tool("submit_reflections", {"reflections": [finding]})
            assert bad.is_error and "Give a concrete lesson" in str(bad.structured_content)
            finding["key_insight"] = "Validate command flags"
            good = await client.call_tool("submit_reflections", {"reflections": [finding]})
            assert good.structured_content["accepted"]
            landed = good.structured_content["ranked"][0]
            assert landed["section"] == "Retry" and landed["section_id"].startswith("res_")

        asyncio.run(check())
        return {
            "status": "completed",
            "final_response": "Submitted",
            "thread_id": "new-codex-session",
            "mcp_calls": [{"server": server_name, "tool": "submit_reflections", "status": "completed"}],
        }

    monkeypatch.setattr("thearc.learning.reflection.providers.codex._run_isolated_sdk", model)
    result = codex_example.generate_reflections(history, agent, model="fixture", output=output)
    assert len(result) == 1 and isinstance(result[0].output, codex_example.ReflectionSchema)
    assert result[0].provenance["tool_transport"] == "mcp"
    assert result[0].history is not None
    assert json.loads((output / "reflections.json").read_text())[0]["key_insight"] == "Validate command flags"
    assert json.loads((output / "submission.json").read_text())["assessments"][0]["items"]
    run = next((output / "reflection-runs").glob("reflection-*"))
    assert (run / "agent-files" / "skills" / "debug" / "SKILL.md").read_text().startswith("# Retry")
    ranked = (output / "ranked-agent.md").read_text()
    retry = ResourceTarget(kind="skill", name="debug", section="Retry").id
    assert f"# Retry [SectionId: {retry}, ranks: (harmful: 1, neutral: 0, helpful: 0)]" in ranked
    ledger = json.loads((output / "ledger.json").read_text())
    assert ledger["assessments"][0]["reference"] == "skills/debug/SKILL.md#L1-L3"
    assert ledger["assessments"][0]["target_id"] == retry
    assert json.loads((output / "ranked-agent.json").read_text())["name"] == "recorded-claude"
    destination = next((output / "reflection-runs").rglob("destination.json"))
    assert json.loads(destination.read_text())["session_id"] == "new-codex-session"


def test_final_text_without_mcp_submission_is_failure(claude_batch, tmp_path, monkeypatch):
    output = tmp_path / "prepared"
    history, agent = codex_example.load(claude_batch, "a", index=tmp_path / "index.sqlite")

    def no_submission(*args, **options):
        options["on_import"]({"session_id": "destination"})
        return {"status": "completed", "final_response": "Here are my reflections...", "thread_id": "destination"}

    monkeypatch.setattr("thearc.learning.reflection.providers.codex._run_isolated_sdk", no_submission)
    with pytest.raises(ReflectionError, match="No reflection list"):
        codex_example.generate_reflections(history, agent, model="fixture", output=output)
    assert not (output / "reflections.json").exists()
    assert json.loads((output / "failure.json").read_text())["code"] == "missing_submission"


def test_prepare_only_cli_never_starts_codex(claude_batch, tmp_path, monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        pytest.fail("prepare-only must not start a runtime")

    monkeypatch.setattr(codex_example.Reflector, "handoff", unexpected)
    output = tmp_path / "prepared"
    codex_example.main(
        [
            "--source",
            str(claude_batch),
            "--session",
            "a",
            "--index",
            str(tmp_path / "index.sqlite"),
            "--output",
            str(output),
            "--prepare-only",
        ]
    )
    assert "4 of 5 events become Codex items" in capsys.readouterr().out
    assert len((output / "trajectory.codex.jsonl").read_text().splitlines()) == 4
    assert "Run the tests" in (output / "trajectory.md").read_text()
    assert (output / "agent-files" / "skills" / "debug" / "SKILL.md").is_file()
    retry = ResourceTarget(kind="skill", name="debug", section="Retry").id
    assert f"# Retry [SectionId: {retry}, ranks: (" in (output / "agent.md").read_text()
