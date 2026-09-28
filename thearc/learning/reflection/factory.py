"""Explicit runtime selection for the shared reflection interface."""

import importlib

from thearc.learning.reflection.backend import ReflectionBackend
from thearc.learning.reflection.engine import AgentReflector, ReflectionError, ReflectorConfig

BUILTIN_BACKENDS = {
    "antigravity": ("antigravity", "AntigravityReflector", "AntigravityReflectorConfig"),
    "claude": ("claude", "ClaudeReflector", "ClaudeReflectorConfig"),
    "codex": ("codex", "CodexReflector", "CodexReflectorConfig"),
}


def create_reflector(
    backend: str, config: ReflectorConfig, *, runner: str | None = None,
    runtime: str | None = None, adapter_version: str | None = None,
) -> AgentReflector:
    """Select a bundled backend or a trusted ``module:function`` adapter.

    No default provider. Custom application code is imported only on execution,
    never for preview. Callers are responsible for adapter isolation and trust.
    """
    if backend != "custom":
        if any(value is not None for value in (runner, runtime, adapter_version)):
            raise ValueError("runner, runtime and adapter_version require backend='custom'")
        if backend not in BUILTIN_BACKENDS:
            raise ValueError(f"Unknown reflection backend: {backend}")
        module_name, reflector_name, config_name = BUILTIN_BACKENDS[backend]
        module = importlib.import_module(f"thearc.learning.reflection.providers.{module_name}")
        return getattr(module, reflector_name)(getattr(module, config_name)(**config.model_dump()))
    if not runner or not runtime or not adapter_version:
        raise ValueError("Custom backends require runner, runtime and adapter_version")
    return AgentReflector(config, backend=ReflectionBackend(agent=runtime, name=runner, version=adapter_version),
                          runner=deferred_runner(runner))


def deferred_runner(runner: str):
    """Validate an adapter address now; import trusted application code only when called."""
    module_name, separator, callable_name = runner.partition(":")
    if (not separator or not callable_name.isidentifier()
            or not all(part.isidentifier() for part in module_name.split("."))):
        raise ValueError("runner must be package.module:function")

    def execute(prompt, schema, settings):
        try:
            callback = getattr(importlib.import_module(module_name), callable_name)
        except (ImportError, AttributeError) as exc:
            raise ReflectionError("adapter_unavailable", f"Cannot load reflection adapter {runner}") from exc
        if not callable(callback):
            raise ReflectionError("adapter_unavailable", "Reflection adapter must be callable")
        return callback(prompt, schema, settings)

    return execute
