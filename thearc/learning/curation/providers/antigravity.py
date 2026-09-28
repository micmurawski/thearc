"""Antigravity curation adapter using the shared structured host-tool loop."""

from thearc.learning.curation.structured import StructuredCurator, curation_instructions
from thearc.learning.reflection.providers import antigravity

AntigravityCuratorConfig = antigravity.AntigravityReflectorConfig


class AntigravityCurator(StructuredCurator):
    def __init__(self, config: AntigravityCuratorConfig, *, runner=None, max_tool_calls=100, max_steps=32):
        execute = runner or (lambda prompt, schema, settings: antigravity._run_antigravity(
            prompt, schema, settings, instructions=curation_instructions(),
        ))
        super().__init__(config, execute,
                         actor={"backend": "antigravity", "sdk_version": antigravity.SDK_VERSION},
                         max_tool_calls=max_tool_calls, max_steps=max_steps)
