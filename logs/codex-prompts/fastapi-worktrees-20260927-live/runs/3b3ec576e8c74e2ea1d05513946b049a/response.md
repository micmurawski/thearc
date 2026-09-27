Implemented `route_class_map` with uppercase method lookup, fallback to `route_class`, and preserved explicit overrides.

Added `AuditedRoute` tests, including POST-only auditing. Updated the knowledge graph.

Validation: syntax and basic lint checks passed. Requested pytest command was attempted but blocked by missing `starlette`; dependency installation failed because the package index was unreachable.