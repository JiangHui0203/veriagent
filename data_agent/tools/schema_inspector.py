"""Bounded schema inspection for the six allowed Olist V0 tables."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..db import read_only_connection, validate_table_name
from ..schemas import ToolResult


def inspect_schema(
    table_name: str,
    database_path: Optional[Path] = None,
) -> ToolResult:
    try:
        table = validate_table_name(table_name)
    except ValueError as error:
        return ToolResult(
            tool="inspect_schema",
            ok=False,
            error_type="INVALID_TABLE",
            error=str(error),
            retryable=False,
        )

    try:
        with read_only_connection(database_path) as connection:
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            columns = connection.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'main' AND table_name = ?
                ORDER BY ordinal_position
                """,
                [table],
            ).fetchall()
        return ToolResult(
            tool="inspect_schema",
            ok=True,
            result={
                "table": table,
                "row_count": row_count,
                "columns": [{"name": name, "type": data_type} for name, data_type in columns],
            },
        )
    except Exception as error:
        return ToolResult(
            tool="inspect_schema",
            ok=False,
            error_type="DATABASE_ERROR",
            error=str(error),
            retryable=True,
        )
