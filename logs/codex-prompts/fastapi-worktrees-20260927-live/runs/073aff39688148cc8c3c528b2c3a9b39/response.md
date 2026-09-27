Implemented `include_in_schema_if` across routes and decorators, evaluated during schema generation.

- New tests: **26 passed**
- Related regression suite: **120 passed**
- Lint and formatting checks passed
- Knowledge graph updated

OpenAPI caching is preserved; clear `app.openapi_schema = None` to regenerate after changing the condition.