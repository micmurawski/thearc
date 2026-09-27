# Task 08: Add `on_startup_error` Callback to Application Lifecycle

- **Category**: Feature Addition — Application Lifecycle
- **Target Repo**: `fastapi` (run agent in `/Users/micmur/GITHUB/thearc/tests/fastapi`)
- **Tool Actions Triggered**: `file.read`, `file.patch`, `shell.exec` (`pytest`)
- **ACE Objective**: Evaluate lifecycle event plumbing and error handler injection.

---

## Agent Prompt

```text
Add an `on_startup_error` callback parameter to FastAPI so applications can gracefully handle failures in startup event handlers.

1. Inspect fastapi/applications.py — the `FastAPI.__init__()` and how `on_startup` handlers are passed through to Starlette.
2. Inspect Starlette's Router to understand where startup handlers are invoked.
3. Add `on_startup_error: Callable[[Exception], None] | None = None` to `FastAPI.__init__()`.
4. Wrap the execution of each registered startup handler in a try/except. On exception:
   - If `on_startup_error` is set, call it with the exception instead of propagating.
   - If `on_startup_error` is not set, propagate the exception unchanged (no behaviour change for existing code).
5. Write tests in tests/test_startup_error_handler.py that:
   - Register a startup handler that raises `RuntimeError("db unavailable")`.
   - With `on_startup_error=None`: confirm the error propagates normally.
   - With a custom `on_startup_error` handler: confirm the exception is captured in a list and startup completes without crashing.
6. Run `pytest -W ignore tests/test_startup_error_handler.py`.
```
