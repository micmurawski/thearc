Implemented per-route `default_response_headers` across `APIRoute` and all FastAPI/APIRouter HTTP decorators. Existing response headers win case-insensitively, including through nested routers.

Validation using the project virtual environment:
- Requested test file: **22 passed**
- Related regression tests: **113 passed, 3 skipped**
- Ruff checks passed
- Graphify updated