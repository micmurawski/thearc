# Task 10: Add `route_class_map` to APIRouter for Per-Method Route Classes

- **Category**: Feature Addition — Extensible Router
- **Target Repo**: `fastapi` (run agent in `/Users/micmur/GITHUB/thearc/tests/fastapi`)
- **Tool Actions Triggered**: `file.read`, `file.patch`, `shell.exec` (`pytest`)
- **ACE Objective**: Evaluate advanced router extensibility and per-method route class dispatch.

---

## Agent Prompt

```text
Extend APIRouter to support different route class implementations per HTTP method.

1. Inspect fastapi/routing.py — `APIRouter.add_api_route()` and the `route_class` parameter.
2. Currently a single `route_class: Type[APIRoute] = APIRoute` applies to all routes on a router.
   Add `route_class_map: dict[str, Type[APIRoute]] | None = None` to `APIRouter.__init__()`.
3. In `APIRouter.add_api_route()`, select the route class as follows:
   - If `route_class_map` is set and the route's HTTP method (uppercased) is a key, use `route_class_map[method]`.
   - Otherwise fall back to `self.route_class` (existing behaviour unchanged).
4. Write a custom subclass of `APIRoute` called `AuditedRoute` in the test file that records the path in a class-level list `AuditedRoute.accessed`.
5. Write tests in tests/test_route_class_map.py that:
   - Create `APIRouter(route_class_map={"POST": AuditedRoute})`.
   - Register `GET /items` and `POST /items` on that router.
   - Make both requests and assert `AuditedRoute.accessed` contains `/items` only once (the POST, not the GET).
6. Run `pytest -W ignore tests/test_route_class_map.py`.
```
