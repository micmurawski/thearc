"""Search and read frozen evidence with a persisted audit; no model invocation."""

import argparse
import json
from pathlib import Path

from thearc.learning import EvidenceSnapshot, RetrievalLimits, RunJournal, session_tools
from thearc.learning.evidence.snapshot import check_destination, write_json_exclusive

from ._common import load_config, project_arguments


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--query", default="graphify")
    parser.add_argument("--output", type=Path, required=True)
    project_arguments(parser)
    args = parser.parse_args(argv)
    evidence = EvidenceSnapshot.load(args.snapshot)
    agent = load_config(args)
    output = check_destination(args.output, tuple(evidence.manifest["blocked_root_hashes"]))
    output.mkdir(parents=True)
    tools = session_tools(evidence, agent=agent, limits=RetrievalLimits(max_calls=8),
                          journal=RunJournal(output / "retrieval.jsonl"))
    hits = tools.call("search_events", {"query": args.query, "limit": 3})
    result = {"search": hits}
    if hits["items"]:
        hit = hits["items"][0]
        address = {"session_id": hit["session_id"], "event_id": hit["event_id"]}
        result["event"] = tools.call("read_event", {**address, "max_chars": 4000})
        result["neighbors"] = tools.call("read_context", {**address, "before": 1, "after": 1})
    result["resources"] = tools.call("list_resources", {"limit": 5})
    result["skill"] = tools.call("read_resource", {"kind": "skill", "name": args.skill, "max_chars": 4000})
    write_json_exclusive(output / "inspection.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
