"""Read-only, file-oriented rendering of MetaAgent differences."""

from __future__ import annotations

import json
from difflib import unified_diff

from thearc.models.agent import MetaAgent
from thearc.models.workspace import MANIFEST, _workspace_files


def diff_agents(
    before: MetaAgent, after: MetaAgent, *, include_ranks: bool = False,
    include_version: bool = True, context_lines: int = 3,
) -> str:
    if not isinstance(after, MetaAgent):
        raise TypeError("Diff requires another MetaAgent")
    if isinstance(context_lines, bool) or not isinstance(context_lines, int) or context_lines < 0:
        raise ValueError("context_lines must be a non-negative integer")

    def render(agent):
        view = agent.model_copy(deep=True) if include_ranks else agent.without_ranks()
        if not include_version:
            view.version = None
        files = _workspace_files(view)
        # Mapping insertion order is not a configuration change.
        files[MANIFEST] = json.dumps(json.loads(files[MANIFEST]), sort_keys=True,
                                    ensure_ascii=False, indent=2) + "\n"
        return files

    old, new = render(before), render(after)
    output = []
    for path in sorted(old.keys() | new.keys()):
        if path in old and path in new and old[path] == new[path]:
            continue
        source = f"a/{path}" if path in old else "/dev/null"
        destination = f"b/{path}" if path in new else "/dev/null"
        output.append(f"diff --git a/{path} b/{path}\n")
        lines = list(unified_diff(old.get(path, "").splitlines(keepends=True),
                                  new.get(path, "").splitlines(keepends=True),
                                  fromfile=source, tofile=destination, n=context_lines))
        if not lines:  # Addition/removal of an empty file still matters.
            output.append(f"--- {source}\n+++ {destination}\n")
        for line in lines:
            output.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(output)
