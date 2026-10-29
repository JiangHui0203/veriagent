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
    experiences: list[dict[str, Any]] | None = None,
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
    if experiences:
        payload["relevant_experiences"] = experiences
        payload["experience_policy"] = "Use these prior lessons as advice. The current question and metric contracts remain authoritative."
    return [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def sql_generation_messages(
    question: str,
    resolved_metrics: list[dict[str, Any]],
    schemas: list[dict[str, Any]],
    experiences: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    payload = {
        "question": question,
        "resolved_metric_definitions": resolved_metrics,
        "relevant_schemas": schemas,
        "requirements": [
            "Generate one read-only SELECT or WITH query.",
            "Respect metric grain, time field, and default scope.",
            "For this V0 scalar workflow, return one aggregate row with exactly one column per selected metric; use metric_id as each column alias.",
        ],
        "output_schema": {"sql": "read-only SQL", "reason": "brief rationale"},
    }
    if experiences:
        payload["relevant_experiences"] = experiences
        payload["experience_policy"] = "Use these prior lessons as advice. Recompute all values for the current question; follow current contracts and schemas."
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
        "requirements": [
            "Copy the SQL column names and numeric values exactly into key_values; include all columns and no extra keys.",
            "Do not round counts or invent derived values. The runtime will verify key_values against SQL.",
        ],
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
