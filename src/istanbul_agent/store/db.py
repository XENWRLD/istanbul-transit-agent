"""DuckDB connection for the local data store.

One file, `data/transit.duckdb`, holds everything queried locally. Every table
carries `source` and `data_as_of` columns so a tool can report provenance
without the caller having to know which dataset a row came from.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from istanbul_agent.config import get_settings

__all__ = [
    "connect",
    "database_path",
    "missing_store_message",
    "read_only_connection",
    "store_ready",
]


def database_path() -> Path:
    return get_settings().duckdb_file


def connect(path: Path | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the store. The parent directory is created when writing."""
    target = path or database_path()
    if not read_only:
        target.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(target), read_only=read_only)


@contextmanager
def read_only_connection(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Read-only connection, closed on exit.

    Tools use this so a query cannot alter the store, and so several readers
    can share the file.
    """
    connection = connect(path, read_only=True)
    try:
        yield connection
    finally:
        connection.close()


def store_ready(path: Path | None = None) -> bool:
    """Yerel depo kurulu mu?"""
    return (path or database_path()).exists()


def missing_store_message(path: Path | None = None) -> str:
    """Depo yoksa kullanıcıya ne yapacağını söyleyen metin.

    Ham `IOException` yerine bu metin dönüyor: depoyu kurmayı atlayan biri
    yığın izi değil çalıştıracağı komutu görmeli (README kurulum adımı 4).
    """
    target = path or database_path()
    return (
        f"Yerel veri deposu kurulmamış ({target.name} yok). Kurulum: "
        "uv run python -m istanbul_agent.store.ingest_rail && "
        "uv run python -m istanbul_agent.store.ingest_timetable && "
        "uv run python -m istanbul_agent.store.ingest"
    )
