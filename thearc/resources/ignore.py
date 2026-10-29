"""Ignored targets: parts of a project's configuration that are left out of its MetaAgent.

``MetaAgent.ignore`` holds patterns over target addresses (``ResourceTarget.matches``). An ignored
target is not imported (``without_ignored``), and installing neither writes nor deletes it
(``ignored_install_path``): it stays as the user wrote it, owned by nobody. In a context document,
thearc owns top-level sections, so an ignored subsection of an owned section is not protected on
install; ignore the top-level section instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thearc.resources.markdown import outline, split_frontmatter
from thearc.resources.targets import Kind, ResourceTarget

if TYPE_CHECKING:
    from thearc.resources.agent import MetaAgent
    from thearc.resources.harness import HarnessLayout


def drop_sections(text: str, resource: ResourceTarget, ignored: MetaAgent) -> str:
    """``text`` (a resource's Markdown) without the sections ``ignored`` ignores, subsections included."""
    _, body, offset = split_frontmatter(text)
    lines = text.splitlines(keepends=True)
    removed: set[int] = set()
    for heading in outline(body, first_line=offset + 1):
        section = resource.model_copy(update={"section": heading.address})
        if heading.start not in removed and ignored.is_ignored(section):
            removed.update(range(heading.start, heading.end + 1))
    return "".join(line for number, line in enumerate(lines, start=1) if number not in removed)


def without_ignored(agent: MetaAgent) -> MetaAgent:
    """A copy of ``agent`` without the targets its ``ignore`` patterns name: whole resources and skill files are
    dropped, ignored sections cut out of their Markdown."""
    if not agent.ignore:
        return agent.model_copy(deep=True)

    def keep(kind: Kind, name: str) -> bool:
        return not agent.is_ignored(ResourceTarget(kind=kind, name=name))

    def text(kind: Kind, name: str, content: str, markdown: bool = True) -> str:
        return drop_sections(content, ResourceTarget(kind=kind, name=name), agent) if markdown else content

    skills = {}
    for name, skill in agent.skills.items():
        if not keep("skill", name):
            continue
        files = {relative: text("skill_file", f"{name}/{relative}", content, relative.endswith(".md"))
                 for relative, content in skill.files.items() if keep("skill_file", f"{name}/{relative}")}
        skills[name] = skill.model_copy(update={"instructions": text("skill", name, skill.instructions),
                                                "files": files})
    context = {filename: document.model_copy(update={"content": text("context", filename, document.content)})
               for filename, document in agent.context.items() if keep("context", filename)}
    resources = {key: item.model_copy(update={"content": text(item.kind, item.path, item.content)})
                 for key, item in agent.resources.items() if keep(item.kind, item.path)}
    extensions = {key: item.model_copy(update={"content": text("extension", key, item.content,
                                                                item.path.endswith(".md"))})
                  for key, item in agent.extensions.items() if keep("extension", key)}
    return agent.model_copy(deep=True, update={
        "skills": skills,
        "context": context,
        "resources": resources,
        "extensions": extensions,
        "hooks": {name: hook for name, hook in agent.hooks.items() if keep("hook", name)},
        "mcps": {name: mcp for name, mcp in agent.mcps.items() if keep("mcp", name)},
    })


def ignored_install_path(agent: MetaAgent, harness: HarnessLayout, relative: str) -> bool:
    """Whether ``relative``, a file an install of ``agent`` writes for ``harness``, belongs to an ignored target."""
    skills = f"{harness.location('skills')}/"
    if relative.startswith(skills):
        name, _, rest = relative.removeprefix(skills).partition("/")
        target = (ResourceTarget(kind="skill", name=name) if rest == "SKILL.md"
                  else ResourceTarget(kind="skill_file", name=f"{name}/{rest}"))
        return agent.is_ignored(target)
    for location, kind in (("rules", "rule"), ("workflows", "workflow"), ("commands", "command")):
        if relative.startswith(prefix := f"{harness.location(location)}/"):
            return agent.is_ignored(ResourceTarget(kind=kind, name=relative.removeprefix(prefix)))  # type: ignore[arg-type]
    if relative.startswith(prefix := f"{harness.location('extensions')}/"):
        name = f"{harness.name}/{relative.removeprefix(prefix)}"
        return agent.is_ignored(ResourceTarget(kind="extension", name=name))
    if relative.startswith(prefix := f"{harness.base}/hooks/") and relative.endswith(".sh"):
        return agent.is_ignored(ResourceTarget(kind="hook", name=relative.removeprefix(prefix).removesuffix(".sh")))
    return False
