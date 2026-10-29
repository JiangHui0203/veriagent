"""Versioned memory snapshots with bounded, deterministic retrieval."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from ..db import ALLOWED_TABLES
from ..tools.metric_search import METRICS, UNSUPPORTED_ALIASES, requests_unsupported_metric


DEFAULT_MEMORY_PATH = Path(__file__).resolve().parents[2] / "memory" / "experiences.json"
FORMAT_VERSION = 1
DOMAIN = "olist_six_tables_v0"
RULE_TEMPLATE_VERSION = 2

# Reviewed failure lessons have their own definition hash. Adding or revising
# one must not invalidate the independent metric-contract memories.
REVIEWED_RULES = {
    "reviewed:child_row_eligibility": (
        "For a minimum number of item or payment rows per order, count rows in the relevant "
        "child table (order_items or order_payments), grouped by order_id, and use the qualifying "
        "order IDs to filter orders. orders.order_id is unique: grouping orders alone and applying "
        "HAVING COUNT(*) cannot measure child rows. Keep eligibility separate from metric aggregation; "
        "preserve the current question's threshold, dates, status and full-order payment scope."
    ),
    "reviewed:alias_scope": (
        "When expressing child-row eligibility, check that every alias referenced by HAVING, "
        "WHERE and SELECT is visible in that query scope. An alias declared inside a different "
        "CTE or subquery is not visible here. Keep child counting with its child table in a "
        "grouped order-ID CTE or correlated EXISTS, then use that result to filter the parent orders."
    ),
}
CHILD_COUNT_TRIGGERS = ["条商品明细", "条支付记录", "item rows", "payment rows"]


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def contract_version() -> str:
    return digest({"metrics": METRICS, "unsupported": UNSUPPORTED_ALIASES,
                   "tables": sorted(ALLOWED_TABLES), "rule_template_version": RULE_TEMPLATE_VERSION})


def rule_definition(rule_id: str) -> dict[str, Any]:
    """Only project-defined lessons may be promoted; model prose is not policy."""
    if rule_id in REVIEWED_RULES:
        return {"rule_id": rule_id, "metric_ids": [], "required_tables": [],
                "stages": ["sql_generation"], "triggers": list(CHILD_COUNT_TRIGGERS),
                "instruction": REVIEWED_RULES[rule_id]}
    if rule_id == "unsupported:net_revenue":
        return {
            "rule_id": rule_id,
            "metric_ids": [],
            "required_tables": [],
            "stages": ["analysis"],
            "triggers": list(UNSUPPORTED_ALIASES),
            "instruction": (
                "The current Olist six-table data cannot answer net revenue: there is no complete "
                "refund ledger or frozen net-revenue metric. Preserve the requested unsupported metric "
                "during metric selection. Do not substitute payment_value or item_revenue; allow the "
                "deterministic contract check to stop with a data gap."
            ),
        }
    if not rule_id.startswith("metric:") or rule_id[7:] not in METRICS:
        raise ValueError("Unknown memory rule")
    metric_id = rule_id[7:]
    definition = METRICS[metric_id]
    triggers = [metric_id, definition["display_name"], *definition["aliases"]]
    if metric_id == "order_count":
        triggers += ["多少订单", "多少个订单", "订单总数", "number of orders"]
    return {
        "rule_id": rule_id,
        "metric_ids": [metric_id],
        "required_tables": list(definition["required_tables"]),
        "stages": ["analysis", "sql_generation"],
        "triggers": triggers,
        "instruction": (
            f"For {definition['display_name']} ({metric_id}), use {definition['grain']} grain and "
            f"{definition['time_field']} as the time field. The contract default scope is "
            f"{definition['default_scope']}. Respect any explicit scope in the current question. "
            f"Use these exact table names: {', '.join(definition['required_tables'])}; do not rename them. "
            + "; ".join(definition["critical_rules"])
            + ". Use the current question's dates and dimensions, never a previous run's numeric answer."
        ),
    }


def rule_version(rule_id: str) -> str:
    if rule_id in REVIEWED_RULES:
        return digest({"contract": contract_version(), "definition": rule_definition(rule_id)})
    return contract_version()


class ExperienceMemory:
    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries = json.loads(json.dumps(entries or [], ensure_ascii=False))
        seen = set()
        for entry in self.entries:
            if entry.get("domain") != DOMAIN or not isinstance(entry.get("sources"), list) or not entry["sources"]:
                raise ValueError("Memory entries require a supported domain and evidence sources")
            rule_id = entry.get("rule_id", "")
            definition = rule_definition(rule_id)
            expected_id = digest([rule_id, entry.get("contract_version")])[:20]
            if entry.get("experience_id") != expected_id or expected_id in seen:
                raise ValueError("Memory entry ID is invalid or duplicated")
            seen.add(expected_id)
            # Stale contracts are kept for inspection but never retrieved.
            if entry.get("contract_version") == rule_version(rule_id):
                if any(entry.get(key) != value for key, value in definition.items()):
                    raise ValueError("Memory text must match its approved rule definition")

    @classmethod
    def load(cls, path: Path) -> "ExperienceMemory":
        path = Path(path)
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION:
            raise ValueError("Unsupported experience memory format")
        if not isinstance(payload.get("entries"), list):
            raise ValueError("Memory entries must be a list")
        return cls(payload["entries"])

    def to_dict(self) -> dict[str, Any]:
        return {"format_version": FORMAT_VERSION, "domain": DOMAIN, "entries": self.entries}

    @property
    def snapshot_id(self) -> str:
        return digest(self.to_dict())

    @property
    def active_entry_count(self) -> int:
        return sum(entry["contract_version"] == rule_version(entry["rule_id"]) for entry in self.entries)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def add_evidence(self, rule_id: str, source: dict[str, Any]) -> bool:
        version = rule_version(rule_id)
        experience_id = digest([rule_id, version])[:20]
        for entry in self.entries:
            if entry["experience_id"] == experience_id:
                if not any(s["trace_sha256"] == source["trace_sha256"] for s in entry["sources"]):
                    entry["sources"].append(source)
                    return True
                return False
        self.entries.append({
            "experience_id": experience_id,
            "domain": DOMAIN,
            "contract_version": version,
            **rule_definition(rule_id),
            "sources": [source],
        })
        self.entries.sort(key=lambda entry: entry["experience_id"])
        return True

    def retrieve(
        self, question: str, *, stage: str, metric_ids: list[str] | None = None, limit: int = 3,
    ) -> list[dict[str, Any]]:
        if not 0 <= limit <= 5:
            raise ValueError("Memory retrieval limit must be between zero and five")
        query = question.lower().replace("_", " ")
        ranked = []
        for entry in self.entries:
            if entry["contract_version"] != rule_version(entry["rule_id"]) or stage not in entry["stages"]:
                continue
            if entry["rule_id"] in REVIEWED_RULES:
                score = 20 if any(term in query for term in entry["triggers"]) else 0
            elif entry["rule_id"] == "unsupported:net_revenue":
                score = 100 if requests_unsupported_metric(question) else 0
            elif metric_ids is not None:
                score = 10 * len(set(metric_ids) & set(entry["metric_ids"]))
            else:
                score = sum(term.lower().replace("_", " ") in query for term in entry["triggers"])
            if score:
                ranked.append((score, entry))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["experience_id"]))
        # Deliberately exclude source questions, answers, paths, and evaluation scores.
        return [{
            "experience_id": entry["experience_id"],
            "rule_id": entry["rule_id"],
            "instruction": entry["instruction"],
            "required_tables": entry["required_tables"],
            "retrieval_score": score,
        } for score, entry in ranked[:limit]]
