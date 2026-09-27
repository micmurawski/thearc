Implemented `deprecated_at` across `APIRoute`, application and router decorators, and included routers. Setting it marks the route deprecated and adds `x-deprecated-at` to OpenAPI.

Validation using the local virtual environment:
- Requested test: **25 passed**
- Router regression tests: **83 passed**
- Ruff and diff checks passed
- Graphify updated