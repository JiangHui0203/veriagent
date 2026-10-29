"""Build and verify a frozen local challenge catalog without any model calls."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from ..data_agent.db import read_only_connection
from ..data_agent.memory.store import digest
from ..data_agent.tools.metric_search import METRICS
from .challenge_tasks import SCENARIOS, VERSION, validate_splits


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_catalog(database: Path, output: Path, scenarios=SCENARIOS) -> dict:
    from .challenge_reference import PythonReference, equal_values, mutation_for, query_values, reference_sql

    validate_splits(scenarios)
    raw = PythonReference(database)
    references = {}
    problems = []
    with read_only_connection(database) as con:
        for s in scenarios:
            sql = reference_sql(s)
            expected = query_values(con, sql)
            independent, evidence = raw.evaluate(s)
            mutation = mutation_for(s)
            wrong_sql = reference_sql(s, mutation)
            wrong = query_values(con, wrong_sql)
            checks = {
                "nonempty_cohort": evidence["eligible_orders"] > 0,
                "independent_reference_agrees": equal_values(expected, independent),
                "mutation_changes_result": not equal_values(wrong, expected),
            }
            if not all(checks.values()):
                problems.append({"task_id": s.task_id, "checks": checks})
            references[s.task_id] = {
                "split": s.split, "family": s.family, "group_key": s.group_key,
                "metrics": s.metrics, "question_sha256": digest(s.question),
                "values": {k: str(v) for k, v in expected.items()},
                "python_values": {k: str(v) for k, v in independent.items()},
                "reference_sql": sql, "mutation": mutation, "mutation_sql": wrong_sql,
                "mutation_values": {k: str(v) for k, v in wrong.items()},
                "evidence": evidence, "checks": checks,
            }
    if problems:
        raise ValueError("Challenge validation failed: " + json.dumps(problems))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for split in ("dev", "test"):
        tasks = [s.task() for s in scenarios if s.split == split]
        (output / f"{split}.jsonl").write_text("".join(json.dumps(task, ensure_ascii=False) + "\n" for task in tasks), encoding="utf-8")
    write_json(output / "specifications.json", [s.specification() for s in scenarios])
    write_json(output / "references.json", references)
    files = {name: file_sha256(output / name) for name in ("dev.jsonl", "test.jsonl", "specifications.json", "references.json")}
    identity = {"version": VERSION, "files": files, "database_sha256": file_sha256(database),
                "metric_contract_sha256": digest(METRICS)}
    manifest = {**identity, "catalog_id": digest(identity), "status": "VALIDATED",
                "source": "Local Olist data, metric contract, and hand-authored scenario recipes",
                "split_policy": "Predicate combinations grouped before model evaluation; same error families, different scenarios",
                "splits": {split: {"tasks": sum(s.split == split for s in scenarios),
                                    "families": dict(Counter(s.family for s in scenarios if s.split == split))}
                           for split in ("dev", "test")}}
    write_json(output / "manifest.json", manifest)
    lines = ["# Olist 挑战题集", "", f"版本：{VERSION}；编号：`{manifest['catalog_id']}`。", "",
             "每题均已通过 SQL / Python 双重参考验证，并证明一种典型错误写法会改变结果。错误写法是测试夹具，不代表模型已经犯过该错误。", "",
             "开发集与测试集按条件组合预先分组；同错误家族跨分组测试属于领域内迁移，不代表跨数据库泛化。", ""]
    for split in ("dev", "test"):
        lines += [f"## {'开发集' if split == 'dev' else '保留测试集'}", ""]
        for s in scenarios:
            if s.split == split:
                lines += [f"- **{s.task_id}**（{s.family}）：{s.question}",
                          f"  数据证据：{references[s.task_id]['evidence']['eligible_orders']} 单；典型错误：`{references[s.task_id]['mutation']}`。"]
        lines.append("")
    (output / "catalog.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def load_tasks(catalog: Path, split: str, database: Path) -> tuple[dict, list[dict]]:
    """Runtime reads only the manifest and requested question file, never gold."""
    if split not in {"dev", "test"}:
        raise ValueError("Unknown split")
    manifest = json.loads((catalog / "manifest.json").read_text())
    identity = {key: manifest[key] for key in ("version", "files", "database_sha256", "metric_contract_sha256")}
    if manifest["status"] != "VALIDATED" or digest(identity) != manifest["catalog_id"]:
        raise ValueError("Catalog identity is invalid")
    if file_sha256(database) != manifest["database_sha256"] or digest(METRICS) != manifest["metric_contract_sha256"]:
        raise ValueError("Database or metric contract changed since challenge validation")
    task_path = catalog / f"{split}.jsonl"
    if file_sha256(task_path) != manifest["files"][task_path.name]:
        raise ValueError("Frozen challenge questions changed")
    tasks = [json.loads(line) for line in task_path.read_text().splitlines() if line.strip()]
    if len(tasks) != manifest["splits"][split]["tasks"] or any(set(task) != {"task_id", "question"} for task in tasks):
        raise ValueError("Runtime task file must contain only question and task ID")
    return manifest, tasks
