"""Bounded POSIX subprocess transport for headless reflection, without a shell."""

import os
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from thearc.learning.reflection.engine import ReflectionError


def run_cli(argv: list[str], *, cwd: Path, timeout: float, input_text: str = "",
            env: dict[str, str] | None = None, max_bytes: int = 4_000_000) -> str:
    if os.name != "posix":
        raise ReflectionError("unsupported_platform", "CLI reflection currently requires macOS or Linux")
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as source:
        source.write(input_text.encode())
        source.seek(0)
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=source, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            raise ReflectionError("missing_runtime", "Unable to start the configured reflection executable") from exc
        output = bytearray()
        size = 0
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, True)
                selector.register(process.stderr, selectors.EVENT_READ, False)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ReflectionError("timeout", "Reflection subprocess exceeded its deadline")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise ReflectionError("output_too_large", "Reflection subprocess exceeded its output limit")
                        if key.data:
                            output.extend(chunk)
                        # Stderr may contain credentials or source text. Never include it in exceptions/artifacts.
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            if process.returncode:
                raise ReflectionError("runtime_error", f"Reflection subprocess exited with code {process.returncode}")
            try:
                return output.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ReflectionError("invalid_output", "Runtime output is not UTF-8") from exc
        except subprocess.TimeoutExpired as exc:
            raise ReflectionError("timeout", "Reflection subprocess exceeded its deadline") from exc
        finally:
            # Kill the owned process group, including descendants retaining pipes after parent exit.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()
