import pytest
from pydantic import ValidationError

from thearc.models import (
    ContextDocument,
    Hook,
    MarkdownDocument,
    MDFile,
    Ranks,
    Skill,
    SkillDefinition,
    get_strategy,
)

SUFFIX = '(harmful: 10, neutral: 9, helpful: 20)'
TEXT = f'# Guidelines {SUFFIX}\n\nBe clear.\n\n## Tests {SUFFIX}\n\nTest behavior.\n'


def test_ranked_markdown_round_trip_and_clean_export(tmp_path):
    doc = MarkdownDocument.parse(TEXT)
    section = doc.get_header('Guidelines', exact=True)
    assert section.title == 'Guidelines'
    assert section.slug == 'guidelines'
    assert section.ranks == Ranks(harmful=10, neutral=9, helpful=20)
    section.ranks.helpful += 1
    ranked = tmp_path / 'ranked.md'
    clean = tmp_path / 'clean.md'
    doc.write(ranked)
    doc.write(clean, include_ranks=False)
    assert MarkdownDocument.read(ranked) == doc
    assert MarkdownDocument.read(clean).get_header('Tests').ranks is None
    assert '(harmful:' not in clean.read_text()
    assert section.ranks.helpful == 21
    assert '(harmful:' not in section.full_content(include_ranks=False)
    assert MarkdownDocument.model_validate_json(doc.model_dump_json()) == doc


def test_only_complete_suffixes_are_metadata():
    for title in ['Notes (optional)', 'Notes (harmful: 2)',
                  'Notes (harmful: 1, harmful: 2, helpful: 3)',
                  'Notes (harmful: -1, neutral: 2, helpful: 3)']:
        section = MarkdownDocument.parse('# ' + title).sections[0]
        assert section.title == title
        assert section.ranks is None
    section = MarkdownDocument.parse('# Notes (helpful: 3, harmful: 1, neutral: 2)').sections[0]
    assert section.ranks == Ranks(harmful=1, neutral=2, helpful=3)


@pytest.mark.parametrize('fence', ['```', '~~~~'])
def test_code_examples_are_not_ranked_sections(fence):
    example = f'{fence}md\n# Example {SUFFIX}\n{fence}'
    doc = MarkdownDocument.parse(f'{example}\n\n# Real {SUFFIX}\n\n{example}')
    assert len(doc.sections) == 1
    clean = doc.to_markdown(include_ranks=False)
    assert clean.count(SUFFIX) == 2
    assert doc.sections[0].content == example


@pytest.mark.parametrize('value', [-1, 1.5, True, '3'])
def test_counts_validate_on_creation_and_assignment(value):
    with pytest.raises(ValidationError):
        Ranks(helpful=value)
    ranks = Ranks()
    with pytest.raises(ValidationError):
        ranks.harmful = value


def test_skills_read_write_and_export(tmp_path):
    text = '---\nname: sample\ndescription: Example\nranks:\n  helpful: 4\n---\n\n' + TEXT
    for model in (Skill, SkillDefinition):
        skill = model.from_markdown('sample', text)
        path = tmp_path / f'{model.__name__}.md'
        skill.write(path)
        reloaded = model.from_markdown('sample', path.read_text())
        ranks = reloaded.ranks if model is Skill else reloaded.metadata.ranks
        assert ranks.helpful == 4
        clean = skill.to_markdown(include_ranks=False)
        assert 'ranks:' not in clean
        assert SUFFIX not in clean
        assert SUFFIX in skill.to_markdown()


@pytest.mark.parametrize('agent', ['claude', 'codex', 'pi', 'antigravity'])
def test_strategy_rank_options(agent):
    skill = SkillDefinition.from_markdown('sample', TEXT)
    skill.metadata.ranks = Ranks(helpful=5)
    strategy = get_strategy(agent)
    ranked = strategy.skill_to_markdown(skill)
    assert 'helpful: 5' in ranked
    assert SUFFIX in ranked
    clean = strategy.skill_to_markdown(skill, include_ranks=False)
    assert 'helpful:' not in clean


def test_hook_exports():
    hook = Hook(name='test', command='pytest', ranks=Ranks(helpful=5))
    assert Hook(**hook.to_dict()) == hook
    assert 'ranks' not in hook.to_dict(include_ranks=False)
    assert hook.ranks.helpful == 5
    assert 'ranks' not in Hook(name='unranked').to_dict()


def test_context_and_tracker(tmp_path):
    context = ContextDocument.from_markdown('AGENTS.md', TEXT)
    assert SUFFIX not in context.to_markdown(include_ranks=False)
    context.write(tmp_path / 'AGENTS.md')
    assert ContextDocument.from_file(tmp_path / 'AGENTS.md').content == TEXT
    tracked = MDFile(text=TEXT)
    section = tracked.get_section('guidelines/tests')
    assert section.ranks.helpful == 20
    assert SUFFIX in section.to_markdown()
    assert SUFFIX not in section.to_markdown(include_ranks=False)
    diff = tracked.compare(MDFile(text=TEXT.replace('helpful: 20', 'helpful: 21')))
    assert not diff.added_sections and not diff.removed_sections
    assert len(diff.modified_sections) == 2
    assert all(change.is_ranks_changed for change in diff.modified_sections)
    tracked.save(tmp_path / 'clean.md', include_ranks=False)
    assert SUFFIX not in (tmp_path / 'clean.md').read_text()
