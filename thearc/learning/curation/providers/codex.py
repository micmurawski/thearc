"""Codex curation adapter using scoped host tools, never an unrestricted editor/shell."""

from thearc.learning.curation.engine import CURATOR_PROMPT, AgentCurator
from thearc.learning.reflection.providers.codex import SDK_VERSION, CodexReflectorConfig, _run_isolated_sdk


class CodexCuratorConfig(CodexReflectorConfig):
    """Model, reasoning, input and deadline settings for an isolated curation turn."""


class CodexCurator(AgentCurator):
    def __init__(self, config: CodexCuratorConfig, *, sdk_runner=None, max_tool_calls: int = 100):
        self.config = config
        runner = sdk_runner or _run_isolated_sdk
        super().__init__(
            lambda prompt, schema, tools: runner(
                prompt, schema, config, instructions=CURATOR_PROMPT, scoped_tools=tools,
            ),
            actor={"backend": "codex", "sdk_version": SDK_VERSION, "settings": config.model_dump(mode="json")},
            max_input_chars=config.max_input_chars, max_tool_calls=max_tool_calls,
        )
