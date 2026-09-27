Implemented `on_startup_error` for sync and async startup handlers. Handled exceptions allow startup to continue; without a callback, exceptions propagate unchanged.

Validation using the project virtual environment:

- Requested test file: **9 passed**
- Existing router event tests: **10 passed**
- Lint and formatting checks passed
- Knowledge graph updated