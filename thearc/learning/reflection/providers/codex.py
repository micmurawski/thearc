"""Codex execution adapter for the provider-neutral reflection pipeline."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Timer
from typing import Any, Literal

from thearc.learning.privacy import hide_historical_ranks, redact
from thearc.learning.reflection.backend import ReflectionBackend
from thearc.learning.reflection.engine import (
    PROMPT_VERSION,
    REFLECTOR_PROMPT,
    SCHEMA_VERSION,
    AgentReflector,
    ReflectionError,
    ReflectionOutput,
    ReflectorConfig,
    reflection_schema,
    resource_catalog,
)

# Keep historical imports available while shared logic lives in reflector.py.
__all__ = [
    "PROMPT_VERSION", "REFLECTOR_PROMPT", "SCHEMA_VERSION", "SDK_VERSION",
    "CodexReflector", "CodexReflectorConfig", "ReflectionError", "ReflectionOutput",
    "hide_historical_ranks", "redact", "reflection_schema", "resource_catalog",
]

SDK_VERSION = "0.157.1"


class CodexReflectorConfig(ReflectorConfig):
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"] = "medium"


class CodexReflector(AgentReflector):
    """Backwards-compatible Codex convenience adapter with the pinned SDK runner."""

    attempt_event_prefix = "sdk"

    def __init__(
        self, config: CodexReflectorConfig, *, artifact_dir: str | Path | None = None,
        sdk_runner: Callable[..., dict[str, Any]] | None = None, resume: bool = False,
    ):
        super().__init__(
            config, backend=ReflectionBackend(agent="codex", name="openai-codex", version=SDK_VERSION),
            runner=sdk_runner or _run_sdk, artifact_dir=artifact_dir, resume=resume,
        )

    def prepare(self, batch, agent):
        prepared = super().prepare(batch, agent)
        prepared["manifest"]["sdk_version"] = SDK_VERSION
        return prepared


# Verified against the pinned runtime's feature list and official configuration
# schema. These are invocation overrides; no user/global config files are edited.
_DISABLED_FEATURES = (
    "hooks", "shell_tool", "unified_exec", "shell_snapshot", "shell_snapshot_v2",
    "apps", "plugins", "remote_plugin", "multi_agent", "multi_agent_v2", "memories",
    "browser_use", "browser_use_external", "computer_use", "in_app_browser",
    "image_generation", "view_image", "code_mode", "code_mode_host", "code_mode_only",
    "skill_search", "skill_mcp_dependency_install", "tool_suggest", "goals", "sleep_tool",
    "realtime_conversation", "unbounded_connection_retries",
)


def _run_sdk(prompt: str, schema: dict[str, Any], config: CodexReflectorConfig) -> dict[str, Any]:
    """Run a real SDK inspection turn, with no CLI-output parsing or API substitute."""
    return _run_isolated_sdk(prompt, schema, config, instructions=REFLECTOR_PROMPT)


def _run_isolated_sdk(prompt, schema, config, *, instructions, scoped_tools=None):
    """Shared isolated runtime; only explicitly supplied host capabilities may act."""
    try:
        from openai_codex import CodexConfig, Thread, is_retryable_error
        from openai_codex.client import CodexClient
        from openai_codex.generated.v2_all import ConfigReadResponse, ListMcpServerStatusResponse
    except ImportError as exc:
        raise ReflectionError("missing_sdk", "Install thearc[codex] to generate real reflections") from exc
    if version("openai-codex") != SDK_VERSION:
        raise ReflectionError("sdk_version", f"This adapter requires openai-codex=={SDK_VERSION}")
    # Tool-using models route dynamic host tools through the local Code Mode
    # dispatcher. Its only operational capabilities remain the scoped callbacks;
    # shell/filesystem/network tools stay disabled and the runtime stays read-only.
    enabled = {"code_mode", "code_mode_host"} if scoped_tools is not None else set()
    disabled = [name for name in _DISABLED_FEATURES if name not in enabled]
    overrides = [f"features.{name}=false" for name in disabled] + [
        *[f"features.{name}=true" for name in sorted(enabled)],
        "features.skip_host_skill_discovery=true", "skills.include_instructions=false",
        "skills.bundled.enabled=false", "project_doc_max_bytes=0", 'web_search="disabled"',
        "tools.update_plan.enabled=false", "tools.experimental_request_user_input.enabled=false",
        "notify=[]", 'developer_instructions=""', 'sandbox_mode="read-only"', 'approval_policy="never"',
        "agents.enabled=false", "check_for_update_on_startup=false",
    ]
    with tempfile.TemporaryDirectory(prefix="thearc-reflector-") as workspace:
        def requests(method, params):
            if method == "item/tool/call" and scoped_tools is not None:
                try:
                    result = scoped_tools.call(params["tool"], params["arguments"])
                    import json

                    text = json.dumps(result, ensure_ascii=False)
                    return {"contentItems": [{"type": "inputText", "text": text}], "success": True}
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    return {"contentItems": [{"type": "inputText", "text": str(exc)}], "success": False}
            # Never use the SDK's permissive default approval handler.
            raise ReflectionError("isolation", "Unexpected runtime capability request")

        client = CodexClient(CodexConfig(
            cwd=workspace, config_overrides=tuple(overrides), client_name="thearc_reflector",
        ), approval_handler=requests)
        timed_out = ThreadEvent()

        def expire() -> None:
            timed_out.set()
            client.close()  # Close the owned app-server and unblock SDK response readers.

        timer = Timer(config.timeout_seconds, expire)
        timer.daemon = True
        try:
            client.start()
            timer.start()
            client.initialize()
            effective = client.request(
                "config/read", {"cwd": workspace, "includeLayers": False}, response_model=ConfigReadResponse,
            ).config.model_dump(mode="json")
            if (any(effective.get("features", {}).get(name) is not False for name in disabled)
                    or any(effective.get("features", {}).get(name) is not True for name in enabled)):
                raise ReflectionError("isolation", "Runtime did not honor tool/hook isolation overrides")
            if (effective.get("project_doc_max_bytes") != 0 or effective.get("web_search") != "disabled"
                    or effective.get("skills", {}).get("include_instructions") is not False):
                raise ReflectionError("isolation", "Runtime did not honor instruction/web isolation overrides")
            # Empty tables merge with inherited config. Disable every discovered
            # server explicitly before starting a thread (and hence MCP clients).
            thread_config = {"mcp_servers": {
                name: {"enabled": False} for name in effective.get("mcp_servers", {})
            }}
            started = client.thread_start({
                "cwd": workspace, "model": config.model, "sandbox": "read-only",
                "approvalPolicy": "never", "ephemeral": True, "config": thread_config,
                "baseInstructions": instructions, "developerInstructions": "",
                **({"dynamicTools": scoped_tools.definitions} if scoped_tools is not None else {}),
            })
            if started.instruction_sources or started.sandbox.root.type != "readOnly":
                raise ReflectionError("isolation", "Thread loaded instructions or weakened the read-only sandbox")
            cursor = None
            while True:
                servers = client.request(
                    "mcpServerStatus/list", {"threadId": started.thread.id, "cursor": cursor},
                    response_model=ListMcpServerStatusResponse,
                )
                if any(getattr(s.runtime_status, "value", None) != "disabled"
                       or s.tools or s.resources or s.resource_templates for s in servers.data):
                    raise ReflectionError("isolation", "Reflection thread still exposes an active MCP capability")
                cursor = servers.next_cursor
                if not cursor:
                    break
            thread = Thread(client, started.thread.id)
            result = thread.run(prompt, output_schema=schema, effort=config.reasoning_effort)
            if timed_out.is_set():
                raise ReflectionError("timeout", "Codex inspection exceeded its deadline")
            # Defense in depth: no operational tool output is acceptable as a
            # reflection result, even if a future runtime changes defaults.
            allowed = {"userMessage", "agentMessage", "reasoning"}
            if scoped_tools is not None:
                allowed.add("dynamicToolCall")
            if any(getattr(getattr(item, "root", item), "type", None) not in allowed for item in result.items):
                raise ReflectionError("isolation", "Unexpected tool activity in the reflection turn")
            return {
                "status": result.status.value, "final_response": result.final_response,
                "thread_id": thread.id, "turn_id": result.id, "sdk_version": SDK_VERSION,
                "usage": result.usage.model_dump(mode="json") if result.usage else None,
            }
        except ReflectionError:
            raise
        except Exception as exc:
            if timed_out.is_set():
                code = "timeout"
            else:
                code = "transient" if is_retryable_error(exc) else "sdk_error"
            # SDK error messages may contain source content or credentials.
            raise ReflectionError(code, f"Codex inspection failed ({type(exc).__name__})") from exc
        finally:
            timer.cancel()
            client.close()
