"""Public resource CLI: explicit prompts, schemas, and execution."""
import json
import sys
from types import ModuleType

import pytest
from click.testing import CliRunner

from thearc.cli import main
from thearc.resources import Reflector


@pytest.fixture
def inputs(tmp_path):
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('Reflect on this evidence.\n## Event e1\nDone.')
    return ['--implementation', 'custom', '--model', 'fixture', '--prompt-file', str(prompt),
            '--runner', 'fixture_runner:run', '--output', str(tmp_path / 'result')]


def test_preview_does_not_import_custom_runner(inputs, tmp_path):
    result = CliRunner().invoke(main, ['reflection', 'run', *inputs])
    assert result.exit_code == 0, result.output
    assert 'fixture_runner' not in sys.modules
    preview = json.loads((tmp_path / 'result/preview.json').read_text())
    assert preview['prompt'].endswith('## Event e1\nDone.')


def test_execute_and_show_schema_output(inputs, tmp_path, monkeypatch):
    module = ModuleType('fixture_runner')
    module.run = lambda *args: {'status': 'completed', 'final_response': '{"summary":"done"}'}
    monkeypatch.setitem(sys.modules, 'fixture_runner', module)
    schema = tmp_path / 'schema.json'
    schema.write_text(json.dumps({'type': 'object', 'properties': {'summary': {'type': 'string'}},
                                  'required': ['summary'], 'additionalProperties': False}))
    result = CliRunner().invoke(main, ['reflection', 'run', *inputs, '--execute', '--schema', str(schema)])
    assert result.exit_code == 0, result.output
    shown = CliRunner().invoke(main, ['reflection', 'show', str(tmp_path / 'result')])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)['reflections'][0]['output'] == {'summary': 'done'}
    repeat = CliRunner().invoke(main, ['reflection', 'run', *inputs, '--execute'])
    assert repeat.exit_code != 0


def test_old_reflection_artifact_is_rejected(tmp_path):
    path = tmp_path / 'reflection.json'
    path.write_text('{"id":"old", "session_ids":[], "summary":"legacy"}')
    result = CliRunner().invoke(main, ['reflection', 'show', str(path)])
    assert result.exit_code != 0


@pytest.mark.parametrize('option', ['--evidence-view', '--batch-size', '--resume', '--backend'])
def test_retired_options_are_not_accepted(inputs, option):
    result = CliRunner().invoke(main, ['reflection', 'run', *inputs, option])
    assert result.exit_code == 2
    assert 'No such option' in result.output


def test_curation_cli_reads_resource_artifacts(inputs, tmp_path, monkeypatch):
    from thearc import MetaAgent, Skill
    agent = MetaAgent(name='test', skills=[Skill(name='debug')])
    config = tmp_path / 'agent.json'
    config.write_text(agent.model_dump_json())
    reflector = Reflector(implementation='custom', model='fixture', artifact_dir=tmp_path / 'reflections',
                          runner=lambda *a: {'status': 'completed', 'final_response': 'Keep the guidance.'})
    reflection = reflector.reflect('Review', agent=agent)
    module = ModuleType('fixture_runner')
    module.run = lambda *a: {'status': 'completed', 'final_response': '{"summary":"Retain guidance", "changes":[]}'}
    monkeypatch.setitem(sys.modules, 'fixture_runner', module)
    result = CliRunner().invoke(main, ['curation', 'run', *inputs, '--agent', str(config),
        '--reflections', str(tmp_path / 'reflections' / reflection.id / 'reflection.json')])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['summary'] == 'Retain guidance'


@pytest.mark.parametrize('through', [False, True])
def test_handoff_cli_passes_selected_resource(tmp_path, monkeypatch, through):
    from types import SimpleNamespace

    from thearc.learning.sessions.models import SourceConfig
    from thearc.learning.sessions.store import SessionStore

    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'session.jsonl').write_text('\n'.join(json.dumps({
        'sessionId': 'native', 'uuid': str(i), 'message': {'role': role, 'content': text},
    }) for i, (role, text) in enumerate([
        ('user', 'First'), ('assistant', 'One'), ('user', 'Second'), ('assistant', 'Two'),
    ])) + '\n')
    index = tmp_path / 'index.sqlite'
    with SessionStore(index) as store:
        store.ingest(SourceConfig(id='fixture', harness='claude', root=logs))
        session_id = store.list_sessions()[0].id
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('RP only')
    seen = []
    def handoff(self, prompt, history, **options):
        seen.append([e.text for e in history.selected_events])
        assert prompt == 'RP only'
        return SimpleNamespace(id='reflection', text='Response')
    monkeypatch.setattr(Reflector, 'handoff', handoff)
    result = CliRunner().invoke(main, ['reflection', 'handoff', '--implementation', 'codex',
        '--model', 'fixture', '--prompt-file', str(prompt), '--output', str(tmp_path / 'handoff'),
        '--index', str(index), '--session-id', session_id, '--trajectory', '1', '--execute',
        *(['--through'] if through else [])])
    assert result.exit_code == 0, result.output
    assert seen == [['First', 'One', 'Second', 'Two'] if through else ['Second', 'Two']]


def test_preview_with_agent_is_serializable_and_hides_ranks(inputs, tmp_path):
    from thearc import MetaAgent, Skill
    from thearc.models import Ranks
    agent = MetaAgent(name='subject', skills=[Skill(name='debug', ranks=Ranks(helpful=42))])
    path = tmp_path / 'agent.json'
    path.write_text(agent.model_dump_json())
    result = CliRunner().invoke(main, ['reflection', 'run', *inputs, '--agent', str(path)])
    assert result.exit_code == 0, result.output
    preview = json.loads((tmp_path / 'result/preview.json').read_text())
    assert preview['manifest']['config_sha256']
    assert 'debug' in preview['prompt']
    assert '42' not in preview['prompt']
