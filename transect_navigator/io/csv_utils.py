"""Read back generated CSV without imposing the ingestion cell-size policy."""
import csv
from pathlib import Path


def read_generated_csv(path: Path) -> tuple[list[str] | None, list[dict[str, str]]]:
    """Allow full generated WKT/JSON cells and restore Python's process-wide limit.

    Only used for private staged output verification, not untrusted input ingestion.
    """
    previous = csv.field_size_limit()
    try:
        csv.field_size_limit(max(previous, path.stat().st_size))
        with path.open(encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream, strict=True)
            rows = list(reader)
            return reader.fieldnames, rows
    finally:
        csv.field_size_limit(previous)
