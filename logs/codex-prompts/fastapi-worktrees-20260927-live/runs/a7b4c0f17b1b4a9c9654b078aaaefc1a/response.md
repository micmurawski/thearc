Implemented `FastAPI(add_request_id_header=False)`.

When enabled, each HTTP request gets a UUID4 stored in `request.state.request_id` and returned as `X-Request-ID`, including error responses.

Validation using the existing virtual environment:

- Requested test command: **6 passed**
- Related regression tests: **24 passed**
- Ruff checks passed
- Graphify updated