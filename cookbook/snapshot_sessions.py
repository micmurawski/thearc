"""Freeze explicitly selected sessions from an existing index; no ingestion or inference."""

import argparse
import json
from pathlib import Path

from thearc.learning import SessionStore, render_context, write_files
from thearc.learning.evidence.snapshot import write_json_exclusive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--session-id", action="append", required=True, help="Repeat to select several sessions")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.index.is_file():
        parser.error("An existing index is required; see the cookbook ingestion example")
    with SessionStore(args.index) as store:
        evidence = store.snapshot(session_ids=args.session_id)
    path = write_files(evidence, args.output, format="markdown")
    context = render_context(evidence)
    write_json_exclusive(path / "context.json", {
        "text": context.text, "included_event_ids": context.included_event_ids,
        "omitted_event_ids": context.omitted_event_ids, "snapshot_sha256": evidence.sha256,
    })
    print(json.dumps({"snapshot": str(path), "sha256": evidence.sha256,
                      "sessions": len(evidence.sessions), "events": evidence.manifest["event_count"]}, indent=2))


if __name__ == "__main__":
    main()
