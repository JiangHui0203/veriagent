"""Pairwise join-cardinality checks for allowed Olist V0 tables."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..db import read_only_connection, table_columns, validate_table_name
from ..schemas import ToolResult


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def check_join_cardinality(
    left_table: str,
    right_table: str,
    left_key: str,
    right_key: Optional[str] = None,
    database_path: Optional[Path] = None,
) -> ToolResult:
    try:
        left = validate_table_name(left_table)
        right = validate_table_name(right_table)
        right_key = right_key or left_key
        left_columns = table_columns(left, database_path)
        right_columns = table_columns(right, database_path)
        if left_key not in left_columns:
            raise ValueError(f"column {left_key!r} does not exist in {left}")
        if right_key not in right_columns:
            raise ValueError(f"column {right_key!r} does not exist in {right}")
    except ValueError as error:
        return ToolResult(
            tool="check_join_cardinality",
            ok=False,
            error_type="INVALID_JOIN",
            error=str(error),
            retryable=False,
        )

    left_sql = _quoted(left)
    right_sql = _quoted(right)
    left_key_sql = _quoted(left_key)
    right_key_sql = _quoted(right_key)
    try:
        with read_only_connection(database_path) as connection:
            left_rows, left_unique_keys, left_duplicate_key_groups = connection.execute(
                f"""
                SELECT
                    COUNT(*),
                    COUNT(DISTINCT {left_key_sql}),
                    (
                        SELECT COUNT(*) FROM (
                            SELECT {left_key_sql}
                            FROM {left_sql}
                            GROUP BY {left_key_sql}
                            HAVING COUNT(*) > 1
                        ) duplicate_groups
                    )
                FROM {left_sql}
                """
            ).fetchone()
            right_rows, right_unique_keys, right_duplicate_key_groups = connection.execute(
                f"""
                SELECT
                    COUNT(*),
                    COUNT(DISTINCT {right_key_sql}),
                    (
                        SELECT COUNT(*) FROM (
                            SELECT {right_key_sql}
                            FROM {right_sql}
                            GROUP BY {right_key_sql}
                            HAVING COUNT(*) > 1
                        ) duplicate_groups
                    )
                FROM {right_sql}
                """
            ).fetchone()
            joined_rows = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM {left_sql} l
                JOIN {right_sql} r
                  ON l.{left_key_sql} = r.{right_key_sql}
                """
            ).fetchone()[0]
            left_keys_with_multiple_matches = connection.execute(
                f"""
                WITH right_match_counts AS (
                    SELECT {right_key_sql} AS join_value, COUNT(*) AS match_count
                    FROM {right_sql}
                    WHERE {right_key_sql} IS NOT NULL
                    GROUP BY {right_key_sql}
                    HAVING COUNT(*) > 1
                )
                SELECT COUNT(DISTINCT l.{left_key_sql})
                FROM {left_sql} l
                JOIN right_match_counts r
                  ON l.{left_key_sql} = r.join_value
                """
            ).fetchone()[0]
            unmatched_left_keys = connection.execute(
                f"""
                SELECT COUNT(DISTINCT l.{left_key_sql})
                FROM {left_sql} l
                LEFT JOIN {right_sql} r
                  ON l.{left_key_sql} = r.{right_key_sql}
                WHERE l.{left_key_sql} IS NOT NULL
                  AND r.{right_key_sql} IS NULL
                """
            ).fetchone()[0]

        left_is_unique = left_duplicate_key_groups == 0
        right_is_unique = right_duplicate_key_groups == 0
        if left_is_unique and right_is_unique:
            relationship = "ONE_TO_ONE"
        elif left_is_unique and not right_is_unique:
            relationship = "ONE_TO_MANY"
        elif not left_is_unique and right_is_unique:
            relationship = "MANY_TO_ONE"
        else:
            relationship = "MANY_TO_MANY"
        multiplication_risk = relationship in {"ONE_TO_MANY", "MANY_TO_MANY"}
        risk_note = (
            "Preaggregate the right branch to the join key before combining it with "
            "another one-to-many branch."
            if multiplication_risk
            else "No right-side fanout was detected for this pairwise join."
        )
        join_key: object = (
            left_key
            if left_key == right_key
            else {"left_key": left_key, "right_key": right_key}
        )
        return ToolResult(
            tool="check_join_cardinality",
            ok=True,
            result={
                "left_table": left,
                "right_table": right,
                "join_key": join_key,
                "left_rows": left_rows,
                "right_rows": right_rows,
                "joined_rows": joined_rows,
                "left_unique_keys": left_unique_keys,
                "right_unique_keys": right_unique_keys,
                "left_keys_with_multiple_matches": left_keys_with_multiple_matches,
                "unmatched_left_keys": unmatched_left_keys,
                "relationship": relationship,
                "multiplication_risk": multiplication_risk,
                "risk_note": risk_note,
            },
        )
    except Exception as error:
        return ToolResult(
            tool="check_join_cardinality",
            ok=False,
            error_type="DATABASE_ERROR",
            error=str(error),
            retryable=True,
        )
