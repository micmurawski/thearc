"""Batch visualization tracks retries, failures, and pending items."""

import pytest

from thearc.flow import Flow
from thearc.flow.decorators import node
from thearc.flow.viz import FlowTracker


def test_tracker_item_progress_preserves_retries(tmp_path):
    attempts = []

    @node(batch=True, max_retries=2)
    def task(value: int):
        attempts.append(value)
        if value == 2 and attempts.count(2) == 1:
            raise ValueError("Transient failure")
        return value * 2

    tracker = FlowTracker(Flow(start=task), output=str(tmp_path / "flow.html"))
    shared = {"items": [{"value": 1}, {"value": 2}]}
    tracker.run(shared)
    assert shared["results"] == [2, 4]
    assert attempts == [1, 2, 2]
    assert [item["status"] for item in next(iter(tracker._state.values()))["items"]] == ["success", "success"]


def test_tracker_failed_item_and_unexecuted_tail(tmp_path):
    @node(batch=True)
    def task(value: int):
        if value == 2:
            raise ValueError("Failure")
        return value

    tracker = FlowTracker(Flow(start=task), output=str(tmp_path / "flow.html"))
    with pytest.raises(ValueError, match="Failure"):
        tracker.run({"items": [{"value": value} for value in (1, 2, 3)]})
    state = next(iter(tracker._state.values()))
    assert state["status"] == "failed"
    assert [item["status"] for item in state["items"]] == ["success", "failed", "pending"]
