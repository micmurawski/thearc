"""Antigravity SDK adapter. Never falls back to agy CLI or an unrelated Gemini client."""

import asyncio
import json
import tempfile
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from typing import Literal

from pydantic import Field

from thearc.learning.reflection.backend import ReflectionBackend, ReflectionRunner
from thearc.learning.reflection.engine import REFLECTOR_PROMPT, AgentReflector, ReflectionError, ReflectorConfig

SDK_VERSION = "0.1.19"
ADAPTER_VERSION = "antigravity-sdk-1"


class AntigravityReflectorConfig(ReflectorConfig):
    reasoning_effort: Literal["minimal", "low", "medium", "high", "extra_high"] = "medium"
    max_model_calls: int = Field(default=4, ge=1, le=100)


class AntigravityReflector(AgentReflector):
    def __init__(self, config: AntigravityReflectorConfig, *, artifact_dir: str | Path | None = None,
                 runner: ReflectionRunner | None = None, resume: bool = False):
        super().__init__(config, backend=ReflectionBackend(agent="antigravity", name="google-antigravity",
                                                         version=f"{SDK_VERSION}/{ADAPTER_VERSION}"),
                         runner=runner or _run_antigravity, artifact_dir=artifact_dir, resume=resume)


def _check_sdk() -> None:
    try:
        if version("google-antigravity") != SDK_VERSION:
            raise ReflectionError("sdk_version", f"This adapter requires google-antigravity=={SDK_VERSION}")
    except PackageNotFoundError as exc:
        raise ReflectionError("missing_sdk", "Install thearc[antigravity] to use the Antigravity reflector") from exc


def _sdk_config(config: AntigravityReflectorConfig, schema: dict, workspace: Path, *, instructions=REFLECTOR_PROMPT):
    from google.antigravity import CapabilitiesConfig, LocalAgentConfig
    from google.antigravity.hooks.policy import allow, deny
    from google.antigravity.types import (
        BudgetConfig,
        BuiltinTools,
        CustomSystemInstructions,
        GeminiAPIEndpoint,
        GeminiModelOptions,
        ModelAPIRetryConfig,
        ModelOutputRetryConfig,
        ModelTarget,
        RetryConfig,
        ThinkingLevel,
    )

    dist = distribution("google-antigravity")
    binaries = [entry.locate() for entry in dist.files or []
                if str(entry).replace("\\", "/").endswith("google/antigravity/bin/localharness")]
    if len(binaries) != 1 or not binaries[0].is_file():
        raise ReflectionError("missing_runtime", "The pinned Antigravity wheel must include its localharness binary")
    return LocalAgentConfig(
        model=ModelTarget(name=config.model, endpoint=GeminiAPIEndpoint(
            options=GeminiModelOptions(thinking_level=ThinkingLevel(config.reasoning_effort)))),
        vertex=False,  # Explicit Gemini API route; never silently switch billing/authentication providers.
        system_instructions=CustomSystemInstructions(text=instructions),
        capabilities=CapabilitiesConfig(enabled_tools=[BuiltinTools.FINISH], enable_subagents=False),
        policies=[deny("*"), allow("finish")], tools=[], hooks=[], triggers=[], mcp_servers=[],
        skills_paths=[], subagents=[], workspaces=[str(workspace)],
        app_data_dir=str(workspace / "app-data"), save_dir=str(workspace / "sessions"),
        response_schema=schema, env={"ANTIGRAVITY_HARNESS_PATH": str(binaries[0])},
        budget_config=BudgetConfig(max_model_calls=config.max_model_calls),
        retry_config=RetryConfig(api_retry=ModelAPIRetryConfig(max_retries=0),
                                 model_output_retry=ModelOutputRetryConfig(max_retries=0)),
    )


async def _inspect(prompt: str, schema: dict, config: AntigravityReflectorConfig, workspace: Path,
                   *, instructions=REFLECTOR_PROMPT) -> dict:
    from google.antigravity import Agent
    from google.antigravity.types import StopReason, ToolCall, ToolResult

    options = _sdk_config(config, schema, workspace, instructions=instructions)
    async with Agent(options) as agent:
        response = await agent.chat(prompt)
        size = 0
        async for chunk in response.chunks:
            if isinstance(chunk, (ToolCall, ToolResult)) and chunk.name != "finish":
                raise ReflectionError("isolation", "Antigravity attempted an operational tool")
            size += len(chunk.model_dump_json())
            if size > 4_000_000:
                raise ReflectionError("output_too_large", "Antigravity response exceeded its output limit")
        if response.stop_reason != StopReason.UNSPECIFIED:
            raise ReflectionError("incomplete", "Antigravity stopped without completing the reflection")
        output = await response.structured_output()
        if not isinstance(output, dict):
            raise ReflectionError("invalid_output", "Antigravity did not return structured reflection output")
        usage = response.usage_metadata
        return {"status": "completed", "final_response": json.dumps(output),
                "thread_id": agent.conversation_id, "turn_id": None, "sdk_version": SDK_VERSION,
                "usage": usage.model_dump(mode="json") if usage else None}


def _run_antigravity(prompt: str, schema: dict, config: AntigravityReflectorConfig,
                     *, instructions=REFLECTOR_PROMPT) -> dict:
    _check_sdk()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ReflectionError("async_context", "Call synchronous reflection using asyncio.to_thread in async apps")
    with tempfile.TemporaryDirectory(prefix="thearc-antigravity-reflector-") as temporary:
        async def run():
            return await asyncio.wait_for(
                _inspect(prompt, schema, config, Path(temporary), instructions=instructions), config.timeout_seconds,
            )

        try:
            return asyncio.run(run())
        except ReflectionError:
            raise
        except TimeoutError as exc:
            raise ReflectionError("timeout", "Antigravity inspection exceeded its deadline") from exc
        except ImportError as exc:
            raise ReflectionError("missing_sdk", "Install thearc[antigravity] with its required dependencies") from exc
        except Exception as exc:
            raise ReflectionError("runtime_error", f"Antigravity inspection failed ({type(exc).__name__})") from exc
