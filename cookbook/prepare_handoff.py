"""Package a task, evidence and configuration for review; never execute the handoff."""

import argparse
import json
from pathlib import Path

from thearc.learning import EvidenceSnapshot, prepare_handoff, save_handoff

from ._common import load_config, project_arguments


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True, help="Existing Git worktree root, observed only")
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", type=Path, required=True)
    project_arguments(parser)
    args = parser.parse_args(argv)
    plan = prepare_handoff(EvidenceSnapshot.load(args.snapshot), task=args.task, workspace=args.workspace,
                           destination_config=load_config(args))
    save_handoff(plan, args.output)
    print(json.dumps({"plan_sha256": plan.sha256, "preview": plan.preview(),
                      "execution_enabled": False}, indent=2))


if __name__ == "__main__":
    main()
