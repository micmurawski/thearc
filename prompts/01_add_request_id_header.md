# Task 01: Add `X-Request-ID` Header Injection to FastAPI Core

- **Category**: Feature Addition — Core Middleware
- **Target Repo**: `fastapi` (run agent in `/Users/micmur/GITHUB/thearc/tests/fastapi`)
- **Tool Actions Triggered**: `file.read`, `file.patch`, `shell.exec` (`pytest`)
- **ACE Objective**: Evaluate a feature addition to FastAPI internals with regression testing.

---

## Agent Prompt

```text
In the FastAPI source directory (fastapi/), add a built-in mechanism for automatic request ID tracking.

1. Open fastapi/applications.py and inspect the FastAPI class constructor and the `build_middleware_stack` flow.
2. Add a new optional parameter `add_request_id_header: bool = False` to the `FastAPI.__init__()` signature.
3. When `add_request_id_header=True`, inject a middleware that:
   - Generates a UUID4 for each incoming request.
   - Attaches it as `X-Request-ID` to the response headers.
   - Makes it accessible as `request.state.request_id`.
4. Write a test in tests/test_request_id_middleware.py that:
   - Creates `FastAPI(add_request_id_header=True)`.
   - Verifies `X-Request-ID` is present in response headers.
   - Verifies it is a valid UUID4 string.
5. Run `pytest -W ignore tests/test_request_id_middleware.py` and ensure all tests pass.
```
