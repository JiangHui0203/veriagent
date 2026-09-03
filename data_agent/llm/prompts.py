"""Short JSON-only prompts for the three V0 model outputs."""

from __future__ import annotations

import json
from typing import Any

from ..trace import json_safe


SYSTEM_MESSAGE = (
    "You are a data-analysis component. Return only the requested JSON object. "
    "Give concise analysis/reason fields, not hidden chain-of-thought. "
    "Never substitute an unavailable business metric with a supported metric; "
    "preserve the requested metric phrase so the deterministic contract lookup can reject it. "
    "Never invent unavailable data."
)


def analysis_messages(
    question: str,
    available_metrics: list[dict[str, Any]],
) -> list[dict[str, str]]:
    payload = {
        "question": question,
        "available_metrics": available_metrics,
        "output_schema": {
            "analysis": "brief rationale",
            "metrics": ["metric_id or unsupported metric phrase"],
            "needed_tables": ["table_name"],
        },
    }
    return [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def sql_generation_messages(
    question: str,
    resolved_metrics: list[dict[str, Any]],
    schemas: list[dict[str, Any]],
) -> list[dict[str, str]]:
    payload = {
        "question": question,
        "resolved_metric_definitions": resolved_metrics,
        "relevant_schemas": schemas,
        "requirements": [
            "Generate one read-only SELECT or WITH query.",
            "Respect metric grain, time field, and default scope.",
        ],
        "output_schema": {"sql": "read-only SQL", "reason": "brief rationale"},
    }
    return [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def final_answer_messages(
    question: str,
    resolved_metrics: list[dict[str, Any]],
    sql_result: dict[str, Any],
    cross_check_result: Any = None,
) -> list[dict[str, str]]:
    payload = {
        "question": question,
        "resolved_metric_definitions": resolved_metrics,
        "sql_result": json_safe(sql_result),
        "cross_check_result": json_safe(cross_check_result),
        "output_schema": {
            "answer": "concise answer",
            "key_values": {},
            "limitations": [],
        },
    }
    return [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
