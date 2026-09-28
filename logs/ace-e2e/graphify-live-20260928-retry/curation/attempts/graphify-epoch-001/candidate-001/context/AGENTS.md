## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase navigation questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. For narrowly specified edits where the user identifies the relevant files or methods, inspect those sources directly; no preliminary graph query or skill load is required unless explicitly requested. Use graph retrieval if cross-file relationships or unknown locations need investigation.
- Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. Keep queries focused on relevant symbols. If results are broad, unrelated, or truncated, inspect the relevant source rather than expanding retrieval merely to satisfy query-first guidance.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files alone are not a reason to skip graphify. Besides the targeted-edit exception above, skip graph retrieval if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
