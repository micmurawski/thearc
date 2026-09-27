Implemented `rate_limit` across `APIRoute`, FastAPI and APIRouter decorators, including nested routers. OpenAPI exposes `x-rate-limit` when provided and omits it otherwise.

Validation: 22 new tests and 42 regression tests passed. Lint and formatting passed. Graphify updated.