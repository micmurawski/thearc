"""Render example flows with FlowTracker to see every state of the flow visualizer.

    .venv/bin/python cookbook/flow_viz.py
    .venv/bin/python cookbook/flow_viz.py --out /tmp/flow-examples

Writes one HTML page per flow to --out (default flow-examples/):

- etl.html          branching on actions, a repair loop (back edge), a batch node, node metadata
- etl-live.html     the same flow captured mid-run: a running batch with per-item progress
- failure.html      a batch item fails, so the node fails and everything after it is skipped
- agent.html        an AsyncFlow: parallel batch research, a critique loop and a nested sub-flow

No model calls; nodes sleep briefly so elapsed times are visible.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import shutil
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thearc.flow import AsyncFlow, AsyncNode, AsyncParallelBatchNode, BatchNode, Flow, Node
from thearc.flow.decorators import node
from thearc.flow.viz import FlowTracker


# ---- etl: branching, a back edge and a batch ------------------------------------------------------------------------
@node(metadata={"source": "s3://example/orders.csv", "owner": "data-eng"})
def fetch_orders(log: Any) -> dict:
    time.sleep(0.2)
    rows = [{"id": i, "amount": round(random.uniform(5, 500), 2)} for i in range(8)]
    rows[3]["amount"] = None
    log.info(f"fetched {len(rows)} rows")
    return {"rows": rows, "repairs": 0}


class ValidateOrders(Node):
    def prep(self, shared):
        return shared["rows"], shared["log"]

    def exec(self, prep_res):
        rows, log = prep_res
        time.sleep(0.1)
        bad = [r["id"] for r in rows if r["amount"] is None]
        if bad:
            log.warning(f"rows with missing amount: {bad}")
        return bad

    def post(self, shared, prep_res, exec_res):
        shared["items"] = [{"row": r} for r in shared["rows"]]
        return "invalid" if exec_res else "valid"


@node(metadata={"strategy": "fill missing amount with 0.0"})
def repair_orders(rows: list[dict], repairs: int, log: Any) -> dict:
    time.sleep(0.15)
    log.info("filling missing amounts")
    return {"rows": [{**r, "amount": r["amount"] or 0.0} for r in rows], "repairs": repairs + 1}


class ConvertCurrency(BatchNode):
    """Batch node: one exec per row. With a snapshot path it copies the live page while item 4 runs."""

    snapshot: tuple[str, str] | None = None

    def prep(self, shared):
        return [item["row"] for item in shared["items"]]

    def exec(self, row):
        time.sleep(0.08)
        if self.snapshot and row["id"] == 4:
            shutil.copy(*self.snapshot)
        return {**row, "amount_eur": round(row["amount"] * 0.92, 2)}

    def post(self, shared, prep_res, exec_res):
        shared["converted"] = exec_res
        shared["log"].info(f"converted {len(exec_res)} rows")


@node(metadata={"table": "warehouse.orders_eur"})
def load_warehouse(converted: list[dict], log: Any) -> dict:
    time.sleep(0.2)
    log.info(f"loaded {len(converted)} rows into warehouse.orders_eur")
    return {"loaded": len(converted)}


# @node returns a ready instance; keep its class so each flow gets fresh nodes (FlowTracker instruments instances).
FetchOrders, RepairOrders, LoadWarehouse = type(fetch_orders), type(repair_orders), type(load_warehouse)


def etl_flow(snapshot: tuple[str, str] | None = None) -> Flow:
    fetch, validate, repair, convert, load = (
        FetchOrders(),
        ValidateOrders(),
        RepairOrders(),
        ConvertCurrency(),
        LoadWarehouse(),
    )
    convert.snapshot = snapshot
    fetch >> validate
    validate - "invalid" >> repair >> validate
    validate - "valid" >> convert >> load
    return Flow(start=fetch)


# ---- failure: a failing batch item; downstream nodes end up skipped ------------------------------------------------
class ReadFiles(Node):
    def post(self, shared, prep_res, exec_res):
        time.sleep(0.1)
        shared["files"] = ["a.md", "b.md", "c.md", "broken.md", "d.md"]


class ParseFiles(BatchNode):
    def prep(self, shared):
        return shared["files"]

    def exec(self, name):
        time.sleep(0.06)
        if name == "broken.md":
            raise ValueError(f"{name}: unexpected front-matter at line 3")
        return name.upper()


class Summarize(Node):
    pass


class Publish(Node):
    pass


def failure_flow() -> Flow:
    read, parse, summarize, publish = ReadFiles(), ParseFiles(), Summarize(), Publish()
    read >> parse >> summarize >> publish
    return Flow(start=read)


# ---- agent: AsyncFlow, parallel batch, loop, nested flow ------------------------------------------------------------
class Plan(AsyncNode):
    async def exec_async(self, prep_res):
        await asyncio.sleep(0.15)
        return ["pricing", "competitors", "regulation", "market size"]

    async def post_async(self, shared, prep_res, exec_res):
        shared["topics"] = exec_res
        shared["log"].info(f"planned {len(exec_res)} research topics")


class Research(AsyncParallelBatchNode):
    async def prep_async(self, shared):
        return shared["topics"]

    async def exec_async(self, topic):
        await asyncio.sleep(random.uniform(0.1, 0.3))
        return f"notes on {topic}"

    async def post_async(self, shared, prep_res, exec_res):
        shared["notes"] = exec_res
        shared["log"].debug(f"collected {len(exec_res)} notes in parallel")


class Draft(AsyncNode):
    async def post_async(self, shared, prep_res, exec_res):
        await asyncio.sleep(0.15)
        shared["drafts"] = shared.get("drafts", 0) + 1
        shared["log"].info(f"draft v{shared['drafts']}")


class Critique(AsyncNode):
    async def post_async(self, shared, prep_res, exec_res):
        await asyncio.sleep(0.1)
        if shared["drafts"] < 2:
            shared["log"].warning("draft too vague; requesting revision")
            return "revise"
        shared["log"].info("approved")
        return "approve"


class FormatReport(Node):
    def post(self, shared, prep_res, exec_res):
        time.sleep(0.1)


class Upload(Node):
    def post(self, shared, prep_res, exec_res):
        time.sleep(0.1)
        shared["log"].info("uploaded report.pdf")


def agent_flow() -> AsyncFlow:
    plan = Plan(metadata={"model": "claude-sonnet-5-5", "max_topics": 4})
    research = Research(metadata={"concurrency": "unbounded"})
    draft, critique = Draft(), Critique(metadata={"rubric": "specific, sourced, under 2 pages"})
    fmt, upload = FormatReport(), Upload()
    fmt >> upload
    deliver = Flow(start=fmt, metadata={"kind": "sub-flow"})
    plan >> research >> draft >> critique
    critique - "revise" >> draft
    critique - "approve" >> deliver
    return AsyncFlow(start=plan)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("flow-examples"))
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    random.seed(7)

    FlowTracker(etl_flow(), output=str(out / "etl.html"), title="Orders ETL").run({})

    live = out / "etl-live.tmp.html"
    tracker = FlowTracker(
        etl_flow(snapshot=(str(live), str(out / "etl-live.html"))), output=str(live), title="Orders ETL (live)"
    )
    tracker.run({})
    live.unlink()

    try:
        FlowTracker(failure_flow(), output=str(out / "failure.html"), title="Docs digest").run({})
    except ValueError as exc:
        print(f"failure.html: flow stopped as intended ({exc})")

    asyncio.run(FlowTracker(agent_flow(), output=str(out / "agent.html"), title="Research agent").run_async({}))

    for page in sorted(out.glob("*.html")):
        print(page.resolve())


if __name__ == "__main__":
    main()
