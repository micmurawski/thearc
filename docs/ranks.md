# Feedback ranks

`Ranks` holds non-negative integer counts for `harmful`, `neutral`, and
`helpful`. Counts default to zero. A model's `ranks=None` means it has not
been ranked; exporting it does not introduce a rank annotation.

## Markdown context sections

```python
from thearc.models import MarkdownDocument, Ranks

doc = MarkdownDocument.read("AGENTS.md")
section = doc.get_header("Guidelines", exact=True)
section.ranks = Ranks(harmful=10, neutral=9, helpful=20)
section.ranks.helpful += 1

print(doc.to_markdown())                    # includes ranks
print(doc.to_markdown(include_ranks=False)) # omits ranks

doc.write("ranked/AGENTS.md")
doc.write("clean/AGENTS.md", include_ranks=False)
```

Headers use `# Guidelines (harmful: 10, neutral: 9, helpful: 21)`.
Nested headers can have their own counts. Reading either version works;
reading a clean file produces `ranks=None`. Exporting a clean version does
not erase counts in memory. Titles, slugs, and section lookup exclude the
annotation. Only complete suffixes containing all three counts are parsed;
ordinary parentheses and fenced code examples remain content.

`MarkdownSection.to_markdown()` and `full_content()`,
`ContextDocument.to_markdown()` and `write()`, and `MDFile.to_markdown()` and
`save()` also accept `include_ranks=False`. `MDFile` indexes expose ranks,
and comparisons report rank changes as modifications to the same section.
Markdown serialization normalizes whitespace as it did previously.

## Skills

```python
from thearc.models import Skill, SkillDefinition, Ranks, get_strategy

skill = Skill.from_dir("my-skill")
skill.ranks = Ranks(helpful=20)
skill.write("ranked/SKILL.md")
skill.write("clean/SKILL.md", include_ranks=False)
print(skill.to_markdown(include_ranks=False))

definition = SkillDefinition.from_markdown("my-skill", skill.to_markdown())
definition.metadata.ranks.helpful += 1
print(get_strategy("claude").skill_to_markdown(definition, include_ranks=False))
```

Whole-skill counts live in YAML frontmatter under `ranks`. Section counts use
header suffixes. `SkillDefinition` exposes whole-skill counts through
`metadata.ranks` and supports `to_markdown()` and `write()` with the same flag.
Edit its `instructions` to change the rendered body; `document` is the parsed
source snapshot. Agent formatting strategies also accept the flag on
`skill_to_markdown()` and `to_yaml()`.

## Hooks and structured data

```python
import json
from pathlib import Path
from thearc.models import Hook, Ranks

hook = Hook(name="tests", command="pytest", ranks=Ranks(helpful=20))
print(hook.to_dict())
print(hook.to_dict(include_ranks=False))
Path("hook.json").write_text(json.dumps(hook.to_dict()), encoding="utf-8")
loaded = Hook(**json.loads(Path("hook.json").read_text(encoding="utf-8")))
```

`{name: hook.to_dict(include_ranks=False) for name, hook in agent.hooks.items()}`
omits ranks for every hook. Pydantic
`model_dump()` / `model_dump_json()` expose the structured rank fields for
storage or APIs; normal Pydantic validation restores them. Use the explicit
Markdown/dictionary export methods above for output without ranks.
