import pytest
from pydantic import ValidationError

from thearc import MetaAgent, Skill
from thearc.learning import EvaluationResult, TaskScore, compare_agents


def test_same_held_out_tasks_isolation_and_metric_deltas():
    baseline = MetaAgent(name="baseline", skills=[Skill(name="demo")])
    candidate = baseline.model_copy(deep=True)
    candidate.skills["demo"].description = "improved"
    tasks = {"holdout-a": {"prompt": "a"}, "holdout-b": {"prompt": "b"}}
    calls = []

    def evaluate(agent, task):
        improved = bool(agent.skills["demo"].description)
        calls.append((task["prompt"], improved))
        agent.skills.clear()
        task.clear()
        return TaskScore(success=improved, cost=2 if improved else 1, latency_seconds=3 if improved else 5)

    result = compare_agents(baseline, candidate, tasks, evaluate, training_task_ids=["training"])
    assert calls == [("a", False), ("a", True), ("b", True), ("b", False)]
    assert result.summary["delta"] == {"success_rate": 1, "mean_cost": 1, "mean_latency_seconds": -2}
    assert tasks["holdout-a"] == {"prompt": "a"}
    assert baseline.skills["demo"].description == ""
    assert candidate.skills["demo"].description == "improved"
    assert EvaluationResult.model_validate_json(result.model_dump_json()) == result


def test_invalid_or_overlapping_evaluation_is_rejected_before_execution():
    def must_not_run(*args):
        pytest.fail("evaluation must not execute")

    with pytest.raises(ValueError, match="overlap"):
        compare_agents(MetaAgent(), MetaAgent(), {"same": "task"}, must_not_run, training_task_ids=["same"])
    with pytest.raises(ValueError, match="at least one"):
        compare_agents(MetaAgent(), MetaAgent(), {}, must_not_run)
    with pytest.raises(ValidationError):
        TaskScore(success=True, cost=float("nan"), latency_seconds=1)
    with pytest.raises(ValidationError):
        EvaluationResult(baseline={}, candidate={})
