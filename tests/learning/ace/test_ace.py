from thearc import MetaAgent, Operation, ResourceTarget, Skill
from thearc.learning import (
    AceConfig,
    AcePipeline,
    Curation,
    Reflection,
    Session,
    TaskScore,
    build_ace_flow,
)


def session(number):
    return Session(id=f"s{number}", source_id="source", harness="pi", native_id=f"native-{number}")


class Selector:
    def select(self, filters=None):
        return [session(number) for number in range(5)]


class Materializer:
    def materialize(self, session, config):
        from thearc.learning import SessionBundle

        return SessionBundle(session=session)


class Reflector:
    def __init__(self):
        self.batches = []

    def reflect(self, batch, target):
        self.batches.append(batch)
        return Reflection(id=f"r{len(self.batches)}", session_ids=[item.session.id for item in batch])


class Curator:
    def __init__(self):
        self.batches = []

    def curate(self, batch, agent):
        self.batches.append(batch)
        return [Curation(
            id=f"c{len(self.batches)}",
            target=ResourceTarget(kind="skill", name="demo"),
            operation=Operation.EDIT,
            value={"description": "Updated"},
            reflection_ids=[item.id for item in batch],
            rationale="Update guidance based on session evidence",
        )]



def test_pipeline_batches_sessions_and_updates_once():
    reflector = Reflector()
    curator = Curator()
    result = AcePipeline(
        Selector(),
        Materializer(),
        reflector,
        curator,
        AceConfig(sessions_per_reflection=2, reflections_per_curation=2),
    ).run(agent=MetaAgent(name="agent", skills=[Skill(name="demo")]))

    assert len(result.sessions) == 5
    assert [len(batch) for batch in reflector.batches] == [2, 2, 1]
    assert [len(batch) for batch in curator.batches] == [2, 1]
    assert result.update.applied_curation_ids == [item.id for item in result.curations]


def test_flow_exposes_the_same_stages():
    reflector = Reflector()
    curator = Curator()
    shared = {
        "selector": Selector(),
        "materializer": Materializer(),
        "reflector": reflector,
        "curator": curator,
        "config": AceConfig(sessions_per_reflection=2, reflections_per_curation=2),
        "agent": MetaAgent(name="agent", skills=[Skill(name="demo")]),
    }
    build_ace_flow().run(shared)
    assert len(reflector.batches) == 3
    assert len(curator.batches) == 2


def test_only_accepted_changes_reach_next_batch_and_are_applied_once(monkeypatch):
    calls, recorded = [], []
    original_apply = MetaAgent.apply_change

    def counted_apply(self, target, operation, value=None):
        calls.append(target)
        return original_apply(self, target, operation, value)

    monkeypatch.setattr(MetaAgent, "apply_change", counted_apply)

    class CheckingCurator:
        def __init__(self):
            self.index = 0

        def curate(self, batch, agent):
            self.index += 1
            assert "rejected" not in agent.hooks
            if self.index == 1:
                return [Curation(
                    id="reject", target=ResourceTarget(kind="hook", name="rejected"),
                    operation=Operation.ADD, value={"command": "echo bad"},
                    reflection_ids=[batch[0].id], rationale=" ",
                )]
            if self.index > 2:
                assert agent.skills["demo"].description == str(self.index - 1)
            return [Curation(
                id=str(self.index), target=ResourceTarget(kind="skill", name="demo"),
                operation=Operation.EDIT, value={"description": str(self.index)},
                reflection_ids=[batch[0].id], rationale="Session evidence",
            )]

    source = MetaAgent(name="agent", skills=[Skill(name="demo")])
    result = AcePipeline(
        Selector(), Materializer(), Reflector(), CheckingCurator(),
        AceConfig(sessions_per_reflection=1, reflections_per_curation=1),
        on_change=lambda change, before, after: recorded.append(change.id),
    ).run(source)
    assert len(calls) == 4
    assert recorded == ["2", "3", "4", "5"]
    assert result.update.rejected_curation_ids == ["reject"]
    assert result.agent.skills["demo"].description == "5"
    assert source.skills["demo"].description == ""


def test_pipeline_evaluates_original_and_result_on_held_out_tasks():
    source = MetaAgent(name="agent", skills=[Skill(name="demo")])
    evaluated = []

    def evaluate(agent, task):
        description = agent.skills["demo"].description
        evaluated.append((description, task))
        return TaskScore(success=description == "Updated", cost=1, latency_seconds=2)

    pipeline = AcePipeline(Selector(), Materializer(), Reflector(), Curator())
    result = pipeline.run(source, evaluation_tasks={"held-out": "Check instructions"}, evaluator=evaluate)
    assert evaluated == [("", "Check instructions"), ("Updated", "Check instructions")]
    assert result.evaluation.summary["delta"]["success_rate"] == 1
    assert source.skills["demo"].description == ""
