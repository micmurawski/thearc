from __future__ import annotations

import asyncio
import random
from typing import Any

from thearc.flow import AsyncFlow
from thearc.flow.decorators import node
from thearc.flow.viz import FlowTracker


@node(
    metadata={
        "description": "Query for files",
        "stage": "discovery",
    },
)
async def query(log: Any) -> dict:
    """Discover files and prepare one argument mapping per batch item."""
    await asyncio.sleep(4)
    files = ["file1.txt", "file2.txt", "file3.txt"]
    log.info(f"Discovered {len(files)} files")
    return {"items": [{"filepath": filepath} for filepath in files]}


@node(
    parallel_batch=True,
    results_key="processed",
    metadata={
        "description": "Process files",
        "stage": "analysis",
    },
)
async def process(filepath: str, log: Any) -> dict:
    """Analyze one file; the decorator runs all items concurrently."""
    log.debug(f"Analyzing {filepath}")
    await asyncio.sleep(random.uniform(1.6, 5))
    result = {
        "file": filepath,
        "issues": random.randint(2, 10),
        "complexity": random.choice(["simple", "medium", "complex"]),
    }
    log.info(f"Processed {filepath}: {result['issues']} issues, {result['complexity']} complexity")
    return result


@node(
    parallel_batch=True,
    items_key="processed",
    results_key="curated",
    metadata={
        "description": "Curate processed file results",
        "stage": "curation",
    },
)
async def curate(file: str, issues: int, complexity: str, log: Any) -> dict:
    """Normalize each processed result concurrently."""
    await asyncio.sleep(random.uniform(0.4, 1.2))
    complexity_scores = {"simple": 1, "medium": 2, "complex": 3}
    curated = {
        "file": file,
        "issues": issues,
        "complexity": complexity,
        "complexity_score": complexity_scores[complexity],
        "requires_attention": issues >= 7 or complexity == "complex",
    }
    if curated["requires_attention"]:
        log.warning(f"{file} requires attention")
    else:
        log.info(f"{file} passed curation")
    return curated


@node(
    metadata={
        "description": "Aggregate curated results",
        "stage": "aggregation",
    },
)
async def aggregate(curated: list[dict], log: Any) -> dict:
    """Fan in all curated results into one summary."""
    average_score = sum(result["complexity_score"] for result in curated) / len(curated)
    summary = {
        "files_processed": len(curated),
        "total_issues": sum(result["issues"] for result in curated),
        "average_complexity": round(average_score, 2),
        "attention_required": sum(result["requires_attention"] for result in curated),
    }
    log.info(
        f"Aggregated {summary['files_processed']} files: "
        f"{summary['total_issues']} issues, {summary['attention_required']} requiring attention"
    )
    return {"summary": summary}


@node(
    metadata={
        "description": "Apply the aggregated result",
        "stage": "application",
    },
)
async def apply(summary: dict, log: Any) -> dict:
    """Produce the final orchestration result."""
    log.info("Applied aggregate summary")
    return {"output": {"status": "applied", **summary}}


query >> process >> curate >> aggregate >> apply


flow = AsyncFlow(start=query)


async def main():
    tracker = FlowTracker(flow, output="flow_status.html", refresh_interval=2)
    import os

    link_path = os.path.abspath("flow_status.html")
    import webbrowser

    print(f"Opening flow status in browser... \nfile://{link_path}\n")
    webbrowser.open(f"file://{link_path}")
    shared = {}
    await tracker.run_async(shared)


if __name__ == "__main__":
    asyncio.run(main())
