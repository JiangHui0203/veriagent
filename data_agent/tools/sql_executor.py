"""Read-only, row-bounded SQL execution for Olist V0 analysis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..db import read_only_connection
from ..schemas import ToolResult


FORBIDDEN_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "COPY",
        "EXPORT",
        "IMPORT",
        "ATTACH",
        "DETACH",
        "INSTALL",
        "LOAD",
        "CALL",
    }
)


def _mask_literals_and_comments(sql: str) -> str:
    output = []
    index = 0
    state = "normal"
    while index < len(sql):
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if state == "normal":
            if char == "'":
                state = "single_quote"
                output.append(" ")
            elif char == '"':
                state = "double_quote"
                output.append(" ")
            elif char == "-" and following == "-":
                state = "line_comment"
                output.extend((" ", " "))
                index += 1
            elif char == "/" and following == "*":
                state = "block_comment"
                output.extend((" ", " "))
                index += 1
            else:
                output.append(char)
        elif state == "single_quote":
            output.append(" ")
            if char == "'" and following == "'":
                output.append(" ")
                index += 1
            elif char == "'":
                state = "normal"
        elif state == "double_quote":
            output.append(" ")
            if char == '"' and following == '"':
                output.append(" ")
                index += 1
            elif char == '"':
                state = "normal"
        elif state == "line_comment":
            output.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "normal"
        elif state == "block_comment":
            output.append(" ")
            if char == "*" and following == "/":
                output.append(" ")
                index += 1
                state = "normal"
        index += 1

    if state in {"single_quote", "double_quote", "block_comment"}:
        raise ValueError("SQL contains an unterminated literal or comment")
    return "".join(output)


def validate_read_only_sql(sql: str) -> None:
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL must be a non-empty string")
    masked = _mask_literals_and_comments(sql)
    statements = [part.strip() for part in masked.split(";") if part.strip()]
    if len(statements) != 1:
        raise ValueError("exactly one SQL statement is allowed")
    statement = statements[0]
    first_keyword = re.match(r"[A-Za-z_]+", statement)
    if first_keyword is None or first_keyword.group(0).upper() not in {"SELECT", "WITH"}:
        raise ValueError("only SELECT or WITH ... SELECT queries are allowed")
    tokens = {token.upper() for token in re.findall(r"\b[A-Za-z_]+\b", statement)}
    forbidden = sorted(tokens & FORBIDDEN_KEYWORDS)
    if forbidden:
        raise ValueError("forbidden SQL keyword: " + ", ".join(forbidden))
    if "SELECT" not in tokens:
        raise ValueError("query must contain SELECT")


def execute_sql(
    sql: str,
    max_rows: int = 200,
    database_path: Optional[Path] = None,
) -> ToolResult:
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
        return ToolResult(
            tool="execute_sql",
            ok=False,
            error_type="INVALID_ARGUMENT",
            error="max_rows must be a positive integer",
            retryable=False,
        )
    try:
        validate_read_only_sql(sql)
    except ValueError as error:
        return ToolResult(
            tool="execute_sql",
            ok=False,
            error_type="SQL_NOT_READ_ONLY",
            error=str(error),
            retryable=False,
        )

    try:
        with read_only_connection(database_path) as connection:
            cursor = connection.execute(sql)
            columns = [column[0] for column in cursor.description]
            fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = fetched[:max_rows]
        return ToolResult(
            tool="execute_sql",
            ok=True,
            result={
                "columns": columns,
                "rows": rows,
                "returned_rows": len(rows),
                "truncated": truncated,
            },
        )
    except Exception as error:
        return ToolResult(
            tool="execute_sql",
            ok=False,
            error_type="SQL_EXECUTION_ERROR",
            error=str(error),
            retryable=True,
        )
