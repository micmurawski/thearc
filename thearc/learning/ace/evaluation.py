"""Compare two MetaAgents on the same caller-supplied held-out tasks."""

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator

from thearc.models import MetaAgent


class TaskScore(BaseModel):
    """Measured task outcome; cost must use the same unit for both agents."""

    success: bool
    cost: float = Field(ge=0, allow_inf_nan=False)
    latency_seconds: float = Field(ge=0, allow_inf_nan=False)


class EvaluationResult(BaseModel):
    baseline: dict[str, TaskScore]
    candidate: dict[str, TaskScore]

    @model_validator(mode="after")
    def matching_tasks(self):
        if not self.baseline or self.baseline.keys() != self.candidate.keys():
            raise ValueError("baseline and candidate must cover the same non-empty task set")
        return self

    @computed_field
    @property
    def summary(self) -> dict[str, dict[str, float]]:
        def summarize(scores):
            return {
                "success_rate": sum(score.success for score in scores.values()) / len(scores),
                "mean_cost": sum(score.cost for score in scores.values()) / len(scores),
                "mean_latency_seconds": sum(score.latency_seconds for score in scores.values()) / len(scores),
            }

        before, after = summarize(self.baseline), summarize(self.candidate)
        return {"baseline": before, "candidate": after,
                "delta": {key: after[key] - before[key] for key in before}}


def compare_agents(
    baseline: MetaAgent,
    candidate: MetaAgent,
    tasks: Mapping[str, Any],
    evaluate: Callable[[MetaAgent, Any], TaskScore],
    *,
    training_task_ids: Iterable[str] = (),
) -> EvaluationResult:
    """Evaluate identical held-out tasks, retaining every individual score.

    The callback owns execution and measurement. No LLM, harness, or promotion
    policy is selected here. IDs must share a namespace with training IDs;
    callers also own detection of semantically duplicated tasks.
    """
    from copy import deepcopy

    if not tasks:
        raise ValueError("evaluation requires at least one held-out task")
    if set(tasks).intersection(training_task_ids):
        raise ValueError("evaluation tasks overlap adaptation tasks")
    before, after = {}, {}
    for index, (task_id, task) in enumerate(tasks.items()):
        # Alternate order to reduce systematic warm-cache/order effects.
        pairs = [(baseline, before), (candidate, after)]
        for agent, scores in pairs if index % 2 == 0 else reversed(pairs):
            scores[task_id] = TaskScore.model_validate(evaluate(agent.model_copy(deep=True), deepcopy(task)))
    return EvaluationResult(baseline=before, candidate=after)
