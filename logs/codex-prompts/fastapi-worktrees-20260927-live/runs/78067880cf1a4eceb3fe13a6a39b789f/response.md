Implemented per-error `code` using `type.upper().replace(".", "_")`, preserving existing fields.

Added tests for short bodies, dotted types, and original error preservation. Updated the knowledge graph.

Validation: syntax checks passed. The requested pytest command failed during collection because `starlette` is missing; dependency installation was unavailable.