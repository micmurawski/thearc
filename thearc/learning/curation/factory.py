"""Provider-neutral curation construction; no implicit runtime selection."""

from thearc.learning.curation.providers import (
    AntigravityCurator,
    AntigravityCuratorConfig,
    ClaudeCurator,
    ClaudeCuratorConfig,
    CodexCurator,
    CodexCuratorConfig,
)
from thearc.learning.curation.structured import StructuredCurator
from thearc.learning.reflection.backend import ReflectionBackend
from thearc.learning.reflection.factory import deferred_runner

BUILTIN_CURATORS = {
    "antigravity": (AntigravityCurator, AntigravityCuratorConfig),
    "claude": (ClaudeCurator, ClaudeCuratorConfig),
    "codex": (CodexCurator, CodexCuratorConfig),
}


def create_curator(backend, config, *, runner=None, runtime=None, adapter_version=None):
    """Build a bundled curator or a structured custom adapter (including Pi).

    The custom callable follows (prompt, schema, config) and requests host tools
    through the supplied step schema. It never receives filesystem capabilities.
    As Python application code, it is trusted and not sandboxed by this library.
    """
    if backend == "custom":
        if not runner or not runtime or not adapter_version:
            raise ValueError("Custom curators require runner, runtime and adapter_version")
        identity = ReflectionBackend(agent=runtime, name=runner, version=adapter_version)
        return StructuredCurator(config, deferred_runner(runner), actor={
            "backend": runtime, "adapter": identity.model_dump(mode="json"),
        })
    if any(value is not None for value in (runner, runtime, adapter_version)):
        raise ValueError("runner, runtime and adapter_version require backend='custom'")
    if backend not in BUILTIN_CURATORS:
        raise ValueError(f"Unknown curation backend: {backend}")
    curator_type, config_type = BUILTIN_CURATORS[backend]
    return curator_type(config_type(**config.model_dump()))
