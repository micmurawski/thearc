"""Exercise downloaded corpora without printing conversation text.

Usage: python scripts/history_benchmark.py [--label LABEL]
Downloads are obtained separately with history_public_data.py.
"""

from __future__ import annotations

import argparse
import gzip
import json
import resource
import statistics
import time
from collections import Counter
from pathlib import Path

from thearc.learning import HistoryService, SearchFilters, SearchQuery, SourceConfig

CORPORA = {
    "commons": ("claude", "native", "commons/sessions/claude_code"),
    "pi": ("pi", "native", "pi"),
    "dataclaw-claude": ("claude", "dataclaw", "dataclaw-claude"),
    "dataclaw-codex": ("codex", "dataclaw", "dataclaw-codex/codex"),
}


def timed(operation):
    start = time.perf_counter()
    result = operation()
    return result, round(time.perf_counter() - start, 6)


def benchmark(root, label):
    harness, source_format, relative = CORPORA[label]
    source = root / "downloads" / relative
    if not source.is_dir() or not list(source.rglob("*.jsonl")):
        return {"label": label, "status": "no downloaded data"}
    index_path = root / "indexes" / f"{label}.sqlite"
    with HistoryService(index_path) as history:
        history.register_source(SourceConfig(id=label, harness=harness, root=source, format=source_format))
        report, sync_seconds = timed(history.sync)
        counts = dict(history.connection.execute("SELECT kind,count(*) FROM events GROUP BY kind").fetchall())
        sessions = history.connection.execute("SELECT count(DISTINCT session_id) FROM events").fetchone()[0]
        event_count = sum(counts.values())
        print(json.dumps({"label": label, "indexed_events": event_count, "seconds": sync_seconds}), flush=True)
        refresh, refresh_seconds = timed(history.sync)
        assert refresh.events_added == 0, "Repeated sync duplicated events"
        queries = []
        for mode in ["literal", "lexical"]:
            for term in ["permission denied", "pytest", "README", "test", "agent"]:
                samples = []
                for _ in range(3):
                    page, seconds = timed(
                        lambda term=term, mode=mode: history.search(SearchQuery(text=term, mode=mode, limit=20))
                    )
                    samples.append(seconds)
                    assert all(hit.event.id for hit in page.hits)
                    if mode == "literal":
                        assert all(term in hit.event.text for hit in page.hits)
                        assert all(hit.match_ranges for hit in page.hits)
                queries.append(
                    {
                        "mode": mode,
                        "term": term,
                        "hits_up_to_20": len(page.hits),
                        "has_more": page.has_more,
                        "median_ms": round(statistics.median(samples) * 1000, 2),
                        "max_ms": round(max(samples) * 1000, 2),
                    }
                )
        evidence_count = 0
        for row in history.connection.execute("SELECT id FROM events ORDER BY id LIMIT 20").fetchall():
            event = history.get_event(row[0])
            assert history.read_evidence(event.id) == event.raw
            context = history.get_context(event.id, before=2, after=2)
            assert any(item.id == event.id for item in context)
            trace = history.get_delegation_trace(event.run_id)
            assert trace.run_id == event.run_id
            evidence_count += 1
        action_page = history.search(
            SearchQuery(text="command", filters=SearchFilters(kinds=["tool_call"], action_kinds=["shell.exec"]))
        )
        assert all(hit.event.action_kind == "shell.exec" for hit in action_page.hits)
        scan_rows = sum(batch.num_rows for batch in history.scan_events(columns=["id", "kind"], batch_size=8192))
        assert scan_rows == event_count
        partial_sources = history.connection.execute("SELECT metadata FROM artifacts").fetchall()
        return {
            "label": label,
            "status": "tested",
            "harness": harness,
            "format": source_format,
            "source_files": len(list(source.rglob("*.jsonl"))),
            "sessions": sessions,
            "events": event_count,
            "counts": counts,
            "sync_report": report.model_dump(),
            "sync_seconds": sync_seconds,
            "repeat_sync_seconds": refresh_seconds,
            "indexed_bytes": index_path.stat().st_size,
            "source_bytes": sum(path.stat().st_size for path in source.rglob("*.jsonl")),
            "evidence_verified": evidence_count,
            "arrow_rows_verified": scan_rows,
            "partial_artifacts": sum(bool(json.loads(row[0]).get("_partial")) for row in partial_sources),
            "diagnostic_count": len(history.diagnostics()),
            "queries": queries,
        }


def tracelab(root):
    path = root / "downloads/tracelab/syfi_coding_trace.jsonl.gz"
    if not path.exists():
        return {"status": "not downloaded"}
    providers, sessions, rows, tools = Counter(), set(), 0, 0
    start = time.perf_counter()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            data = json.loads(line)
            rows += 1
            providers[data.get("provider", "unknown")] += 1
            sessions.add((data.get("provider"), data.get("session_id")))
            tools += len(data.get("tools") or [])
    return {
        "status": "validated normalized archive; not imported as native sessions",
        "rows": rows,
        "sessions": len(sessions),
        "providers": dict(providers),
        "tools": tools,
        "parse_seconds": round(time.perf_counter() - start, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".history-bench"))
    parser.add_argument("--label", choices=[*CORPORA, "tracelab"])
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    labels = [args.label] if args.label else list(CORPORA)
    report_path = args.root / "results.json"
    result_dir = args.root / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    results = json.loads(report_path.read_text()) if report_path.exists() else {}
    if args.summarize:
        for result_file in result_dir.glob("*.json"):
            results[result_file.stem] = json.loads(result_file.read_text())
        report_path.write_text(json.dumps(results, indent=2))
        print(
            json.dumps(
                {
                    label: {key: data.get(key) for key in ["status", "sessions", "events", "rows"]}
                    for label, data in results.items()
                }
            )
        )
        return
    for label in labels:
        result = tracelab(args.root) if label == "tracelab" else benchmark(args.root, label)
        results[label] = result
        result_file = result_dir / f"{label}.json"
        result_file.write_text(json.dumps(result, indent=2))
        report_path.write_text(json.dumps(results, indent=2))
        print(json.dumps(result), flush=True)
    print("peak_rss_platform_units", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, flush=True)


if __name__ == "__main__":
    main()
