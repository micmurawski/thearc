"""Reflect on a saved handoff's source evidence/configuration, NOT on an executed handoff."""

import argparse
import json
from pathlib import Path

from thearc import MetaAgent
from thearc.learning import load_handoff

from ._common import reflect, reflection_arguments


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    reflection_arguments(parser)
    args = parser.parse_args(argv)
    plan = load_handoff(args.handoff)
    if plan.data["destination_config"] is None:
        parser.error("The handoff must contain a destination MetaAgent configuration to evaluate")
    agent = MetaAgent.model_validate(plan.data["destination_config"])
    # Deliberately do not run plan.task, enter its workspace, or execute/install its config.
    print(json.dumps(reflect(plan.evidence, agent, args, handoff_sha256=plan.sha256), indent=2))


if __name__ == "__main__":
    main()
