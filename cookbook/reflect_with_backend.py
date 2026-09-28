"""Use an explicitly supplied reflection runtime; supports offline preview without importing it."""

import argparse
import importlib
import json
from pathlib import Path

from thearc.learning import AgentReflector, EvidenceSnapshot, ReflectionBackend, ReflectorConfig
from thearc.learning.evidence.snapshot import check_destination, write_json_exclusive

from ._common import load_config, project_arguments, reflection_arguments


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--agent", choices=["codex", "claude", "pi", "antigravity"], required=True)
    parser.add_argument("--runner", required=True, help="Trusted Python adapter callable as package.module:function")
    parser.add_argument("--adapter-version", required=True, help="Cache identity for adapter code and runtime settings")
    project_arguments(parser)
    reflection_arguments(parser, builtin=False)
    args = parser.parse_args(argv)
    module, separator, name = args.runner.partition(":")
    if not separator or not module or not name.isidentifier():
        parser.error("--runner must be package.module:function")
    evidence = EvidenceSnapshot.load(args.snapshot)
    agent = load_config(args)
    output = check_destination(args.output, tuple(evidence.manifest["blocked_root_hashes"]))
    reflector = AgentReflector(
        ReflectorConfig(model=args.model, timeout_seconds=args.timeout),
        backend=ReflectionBackend(agent=args.agent, name=args.runner, version=args.adapter_version),
        artifact_dir=output,
    )
    prepared = reflector.prepare_snapshot(evidence, agent)
    evidence.save(output / "evidence")
    write_json_exclusive(output / "preview.json", prepared)
    if args.execute:
        # Importing application code is itself trusted execution. Never load an adapter from session evidence.
        runner = getattr(importlib.import_module(module), name)
        if not callable(runner):
            raise TypeError("The runtime adapter must be callable")
        reflector = AgentReflector(reflector.config, backend=reflector.backend, runner=runner, artifact_dir=output)
        result = reflector.reflect_snapshot(evidence, agent)
        print(result.model_dump_json(indent=2))
    else:
        print(json.dumps({"status": "preview_only", "preview": str(output / "preview.json")}, indent=2))


if __name__ == "__main__":
    main()
