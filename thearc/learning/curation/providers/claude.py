"""Claude curation adapter using the shared structured host-tool loop."""

from thearc.learning.curation.structured import StructuredCurator, curation_instructions
from thearc.learning.reflection.providers import claude

ClaudeCuratorConfig = claude.ClaudeReflectorConfig


class ClaudeCurator(StructuredCurator):
    def __init__(self, config: ClaudeCuratorConfig, *, runner=None, max_tool_calls=100, max_steps=32):
        execute = runner or (lambda prompt, schema, settings: claude._run_claude(
            prompt, schema, settings, instructions=curation_instructions(),
        ))
        super().__init__(config, execute, actor={"backend": "claude", "runtime_version": claude.CLI_VERSION},
                         max_tool_calls=max_tool_calls, max_steps=max_steps)
