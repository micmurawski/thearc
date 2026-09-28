"""Preview or generate per-session reflections using a bundled backend and a portable snapshot."""

import argparse
import json
from pathlib import Path

from thearc.learning import EvidenceSnapshot

from ._common import load_config, project_arguments, reflect, reflection_arguments


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    project_arguments(parser)
    reflection_arguments(parser)
    args = parser.parse_args(argv)
    print(json.dumps(reflect(EvidenceSnapshot.load(args.snapshot), load_config(args), args), indent=2))


if __name__ == "__main__":
    main()
