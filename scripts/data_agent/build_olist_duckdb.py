#!/usr/bin/env python3
"""Build the six-table Olist V0 DuckDB database from the raw CSV files."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb

from olist_common import (
    DATABASE_NAME,
    DEFAULT_DATA_DIR,
    EXPECTED_ROW_COUNTS,
    TABLE_DEFINITIONS,
    sql_path,
    validate_source_files,
)


def build_database(data_dir: Path) -> Path:
    data_dir = data_dir.resolve()
    validate_source_files(data_dir)
    database_path = data_dir / DATABASE_NAME
    connection = duckdb.connect(str(database_path))

    try:
        connection.execute("BEGIN TRANSACTION")
        for table_name, definition in TABLE_DEFINITIONS.items():
            columns = definition["columns"]
            column_sql = ",\n                ".join(
                f'"{name}" {data_type}' for name, data_type in columns
            )
            connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            connection.execute(
                f"""
                CREATE TABLE "{table_name}" (
                    {column_sql}
                )
                """
            )
            csv_path = data_dir / str(definition["csv"])
            if table_name == "category_translation":
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = csv.DictReader(handle)
                    connection.executemany(
                        "INSERT INTO category_translation VALUES (?, ?)",
                        (
                            (
                                row["product_category_name"],
                                row["product_category_name_english"],
                            )
                            for row in rows
                        ),
                    )
            else:
                connection.execute(
                    f"""
                    COPY "{table_name}"
                    FROM '{sql_path(csv_path)}'
                    (FORMAT CSV, HEADER TRUE, DELIMITER ',', QUOTE '"', ESCAPE '"', NULL '')
                    """
                )

            row_count = connection.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
            if row_count != EXPECTED_ROW_COUNTS[table_name]:
                raise ValueError(
                    f"{table_name} loaded {row_count:,} rows; "
                    f"expected {EXPECTED_ROW_COUNTS[table_name]:,}"
                )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()

    return database_path


def print_summary(database_path: Path) -> None:
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        for table_name in TABLE_DEFINITIONS:
            row_count = connection.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
            schema = connection.execute(
                f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_name = '{table_name}' ORDER BY ordinal_position"
            ).fetchall()
            compact_schema = ", ".join(f"{name} {data_type}" for name, data_type in schema)
            print(f"{table_name}: {row_count:,} rows | {compact_schema}")
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing the six Olist CSV files",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database_path = build_database(args.data_dir)
    print_summary(database_path)


if __name__ == "__main__":
    main()
