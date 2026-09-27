Implemented `default_response_model_exclude_none=False`, stored on the app and inherited by all app-level HTTP decorators and `add_api_route`. Explicit route values override it.

Added `tests/test_global_exclude_none.py`.

Validation in the project environment:
- Requested tests: **30 passed**
- Existing response-model tests: **46 passed**
- Ruff checks passed
- Graphify updated