Implemented `APIRouter(strict_slashes=True)`.

- `False` matches both slash forms without redirects.
- Settings persist through route creation and nested `FastAPI.include_router()` calls.
- Added per-route overrides and regression tests.
- Requested tests: **9 passed**; related tests: **52 passed**.
- Ruff checks passed; knowledge graph updated.