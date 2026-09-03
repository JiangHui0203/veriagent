"""Read-only DuckDB connection helpers for the Olist V0 database."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import duckdb


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_DIR / "datasets" / "olistbr"
DEFAULT_DATABASE_PATH = DEFAULT_DATA_DIR / "ecommerce.duckdb"
METRIC_CONTRACT_PATH = PROJECT_DIR / "knowledge" / "metric_definitions.md"

ALLOWED_TABLES = frozenset(
    {
        "customers",
        "orders",
        "order_items",
        "order_payments",
        "products",
        "category_translation",
    }
)


def resolve_database_path(database_path: Optional[Path] = None) -> Path:
    path = Path(database_path or DEFAULT_DATABASE_PATH).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"DuckDB database not found: {path}")
    return path


@contextmanager
def read_only_connection(
    database_path: Optional[Path] = None,
) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(str(resolve_database_path(database_path)), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


def validate_table_name(table_name: str) -> str:
    if not isinstance(table_name, str):
        raise ValueError("table_name must be a string")
    normalized = table_name.strip().lower()
    if normalized not in ALLOWED_TABLES:
        raise ValueError(
            f"table {table_name!r} is not allowed; expected one of "
            + ", ".join(sorted(ALLOWED_TABLES))
        )
    return normalized


def table_columns(table_name: str, database_path: Optional[Path] = None) -> list[str]:
    table = validate_table_name(table_name)
    with read_only_connection(database_path) as connection:
        return [
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'main' AND table_name = ?
                ORDER BY ordinal_position
                """,
                [table],
            ).fetchall()
        ]
