from thearc.history import (
    AceConfig,
    AceContext,
    AcePipeline,
    Curation,
    Playbook,
    Reflection,
    Session,
    UpdateResult,
    build_ace_flow,
)


def session(number):
    return Session(id=f"s{number}", source_id="source", harness="pi", native_id=f"native-{number}")


class Selector:
    def select(self, filters=None):
        return [session(number) for number in range(5)]


class Materializer:
    def materialize(self, session, config):
        from thearc.history import SessionBundle

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

    def curate(self, batch, playbook):
        self.batches.append(batch)
        return Curation(id=f"c{len(self.batches)}", reflection_ids=[item.id for item in batch])


class Updater:
    def __init__(self):
        self.calls = []

    def update(self, playbook, curations):
        self.calls.append(curations)
        return UpdateResult(
            playbook_id=playbook.id,
            previous_revision=playbook.revision,
            new_revision=playbook.revision + 1,
            applied_curation_ids=[item.id for item in curations],
        )


def test_pipeline_batches_sessions_and_updates_once():
    reflector = Reflector()
    curator = Curator()
    updater = Updater()
    result = AcePipeline(
        Selector(),
        Materializer(),
        reflector,
        curator,
        updater,
        AceConfig(sessions_per_reflection=2, reflections_per_curation=2),
    ).run(Playbook(id="playbook"), target=AceContext())

    assert len(result.sessions) == 5
    assert [len(batch) for batch in reflector.batches] == [2, 2, 1]
    assert [len(batch) for batch in curator.batches] == [2, 1]
    assert len(updater.calls) == 1
    assert updater.calls[0] == result.curations


def test_flow_exposes_the_same_stages():
    reflector = Reflector()
    curator = Curator()
    updater = Updater()
    shared = {
        "selector": Selector(),
        "materializer": Materializer(),
        "reflector": reflector,
        "curator": curator,
        "updater": updater,
        "config": AceConfig(sessions_per_reflection=2, reflections_per_curation=2),
        "target": AceContext(),
        "playbook": Playbook(id="playbook"),
    }
    build_ace_flow().run(shared)
    assert len(reflector.batches) == 3
    assert len(curator.batches) == 2
    assert len(updater.calls) == 1
