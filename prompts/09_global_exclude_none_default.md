# Task 09: Add `response_model_exclude_none_fields` Global App Setting

- **Category**: Feature Addition — Response Model Configuration
- **Target Repo**: `fastapi` (run agent in `/Users/micmur/GITHUB/thearc/tests/fastapi`)
- **Tool Actions Triggered**: `file.read`, `file.patch`, `shell.exec` (`pytest`)
- **ACE Objective**: Evaluate global configuration propagation through route handlers.

---

## Agent Prompt

```text
Add a global app-level default for `response_model_exclude_none` so all routes inherit it without requiring per-route annotation.

1. Inspect fastapi/applications.py — the `FastAPI.__init__()` signature and `get` / `post` / `put` decorator helpers.
2. Inspect fastapi/routing.py — how `APIRoute` consumes `response_model_exclude_none`.
3. Add `default_response_model_exclude_none: bool = False` to `FastAPI.__init__()` and store it as `self.default_response_model_exclude_none`.
4. When a route decorator does not explicitly pass `response_model_exclude_none`, use `self.default_response_model_exclude_none` as its default value.
   Per-route explicit values must still override the app-level default.
5. Write tests in tests/test_global_exclude_none.py that:
   - Create `FastAPI(default_response_model_exclude_none=True)`.
   - Register `GET /item` returning a model with optional `None` fields.
   - Assert the JSON response does NOT contain keys whose value is `None`.
   - Create a second app with the default (`False`); assert `None` fields ARE included.
   - On the `True`-default app, register one route explicitly with `response_model_exclude_none=False`; assert `None` fields appear for that route only.
6. Run `pytest -W ignore tests/test_global_exclude_none.py`.
```
