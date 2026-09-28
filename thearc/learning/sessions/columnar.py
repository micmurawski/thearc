"""Optional Arrow batches and immutable Parquet exports of normalized events."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from thearc.learning.sessions.models import SearchFilters


def arrow():
    try:
        import pyarrow as pa
    except ImportError as exc:
        raise RuntimeError("Install the Arrow extra: pip install 'thearc[arrow]'") from exc
    return pa


def scan_events(service, filters: SearchFilters | None = None, columns=None, batch_size: int = 8192):
    """Yield bounded RecordBatches. A scan is not an indexed text search."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    pa = arrow()
    names = [
        "id",
        "source_id",
        "harness",
        "session_id",
        "run_id",
        "kind",
        "role",
        "timestamp",
        "text",
        "tool_name",
        "action_kind",
        "call_id",
        "status",
        "native_id",
        "parent_native_id",
        "parent_run_id",
        "cwd",
        "source_path",
        "json_pointer",
    ]
    fields = {name: pa.string() for name in names}
    fields.update({name: pa.int64() for name in ["generation", "byte_offset", "byte_length", "line"]})
    selected = list(fields) if columns is None else list(columns)
    if not selected or len(set(selected)) != len(selected) or any(name not in fields for name in selected):
        raise ValueError("columns must be nonempty, unique canonical event column names")
    schema = pa.schema([(name, fields[name]) for name in selected])
    rows = []
    for event in service.iter_events(filters):
        row = event.model_dump()
        row.update(
            source_path=str(event.reference.path),
            json_pointer=event.reference.json_pointer,
            generation=event.reference.generation,
            byte_offset=event.reference.byte_offset,
            byte_length=event.reference.byte_length,
            line=event.reference.line,
        )
        rows.append({name: row.get(name) for name in selected})
        if len(rows) == batch_size:
            yield pa.RecordBatch.from_pylist(rows, schema=schema)
            rows = []
    if rows:
        yield pa.RecordBatch.from_pylist(rows, schema=schema)


def export_dataset(service, destination: str | Path, filters: SearchFilters | None = None) -> Path:
    """Publish a new snapshot directory; never overwrite existing exports."""
    arrow()
    import pyarrow.parquet as pq

    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A read transaction pins the canonical revision for this export.
    if service.connection.in_transaction:
        raise RuntimeError("Export requires an idle history connection")
    with tempfile.TemporaryDirectory(prefix=".history-export-", dir=destination.parent) as temporary:
        temporary_path = Path(temporary)
        service.connection.execute("BEGIN")
        try:
            revision = service.revision
            files = []
            for index, batch in enumerate(scan_events(service, filters)):
                filename = f"events-{index:06d}.parquet"
                pq.write_table(arrow().Table.from_batches([batch]), temporary_path / filename)
                files.append(filename)
            manifest = {
                "schema_version": 1,
                "index_revision": revision,
                "files": files,
                "filters": (filters or SearchFilters()).model_dump(mode="json"),
                "warnings": service._warnings(),
            }
            (temporary_path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        finally:
            service.connection.rollback()
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(temporary_path, destination)
    return destination
