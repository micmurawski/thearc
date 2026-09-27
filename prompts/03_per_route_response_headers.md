# Task 03: Add Per-Route Response Header Defaults

- **Category**: Feature Addition — Router
- **Target Repo**: `fastapi` (run agent in `/Users/micmur/GITHUB/thearc/tests/fastapi`)
- **Tool Actions Triggered**: `file.read`, `file.patch`, `shell.exec` (`pytest`)
- **ACE Objective**: Evaluate how response pipeline and route config interact in FastAPI.

---

## Agent Prompt

```text
Add support for per-route default response headers to FastAPI.

1. Inspect fastapi/routing.py — specifically `APIRoute.get_route_handler()` and the response construction flow.
2. Add an optional `default_response_headers: dict[str, str] | None = None` parameter to `APIRoute.__init__()`.
3. When set, inject these headers into every response returned from that route, without overriding headers the route handler sets explicitly.
4. Propagate the parameter through all `@app.get`, `@app.post`, etc. decorator helpers in fastapi/applications.py and fastapi/routing.py.
5. Write tests in tests/test_route_default_headers.py that:
   - Register a GET `/cached-item` with `default_response_headers={"Cache-Control": "max-age=300", "X-Source": "db"}`.
   - Verify both headers appear in the response.
   - Register a second endpoint where the route handler manually sets `Cache-Control: no-store`; confirm the handler's value wins.
6. Run `pytest -W ignore tests/test_route_default_headers.py`.
```
