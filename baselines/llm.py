from __future__ import annotations

import json
import re
import uuid
from collections import Counter

from ..llm import LLMClient
from ..retrieval import LocalRetriever
from ..schemas import Label, RunResult, Task, normalize_label
from ..tools import ProofVerifier


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _llm_result(
    task: Task,
    method: str,
    answer: Label,
    retrieved_ids: set[str],
    tool_calls: int,
    proof_valid: bool,
    action_counts: Counter[str],
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    cost_usd: float = 0.0,
) -> RunResult:
    correct = answer == task.question.label
    return RunResult(
        run_id=str(uuid.uuid4()),
        task_id=task.task_id,
        method=method,
        answer=answer,
        gold_label=task.question.label,
        status="COMPLETED" if answer != Label.INVALID else "PARSE_FAILURE",
        is_correct=correct,
        task_success=correct and proof_valid,
        proof_valid=proof_valid,
        coverage_complete=False,
        retrieved_evidence_ids=sorted(retrieved_ids),
        action_counts=dict(action_counts),
        tool_calls=tool_calls,
        steps=max(1, sum(action_counts.values())),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )


class DirectThinkBaseline:
    method_name = "direct_think_llm"

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def run(self, task: Task) -> RunResult:
        prompt = f"""Solve this ProofWriter task under open-world semantics.
Return JSON: {{"reasoning":"...", "answer":"true|false|unknown"}}.

Context:
{task.world.theory}

Question:
{task.question.text}
"""
        response = self.client.complete([{"role": "user", "content": prompt}])
        try:
            answer = normalize_label(_parse_json(response.content).get("answer"))
        except Exception:
            answer = Label.INVALID
        # This baseline does not produce machine-verifiable citations/proofs.
        return _llm_result(
            task,
            self.method_name,
            answer,
            set(),
            0,
            False,
            Counter({"STOP": 1}),
            response.prompt_tokens,
            response.completion_tokens,
            response.latency_ms,
            response.cost_usd,
        )


class StandardRAGLLMBaseline:
    method_name = "standard_rag_llm"

    def __init__(self, client: LLMClient, top_k: int = 8) -> None:
        self.client = client
        self.top_k = top_k
        self.verifier = ProofVerifier()

    def run(self, task: Task) -> RunResult:
        response = LocalRetriever(task.world).search_text(task.question.text, self.top_k)
        ids = {x.evidence_id for x in response.items}
        docs = "\n".join(f"[{x.evidence_id}] {x.text}" for x in response.items)
        prompt = f"""Answer under open-world semantics using only the evidence below.
Return JSON: {{"reasoning":"...", "answer":"true|false|unknown", "citations":["id"]}}.

Evidence:
{docs}

Question: {task.question.text}
"""
        llm_response = self.client.complete([{"role": "user", "content": prompt}])
        try:
            obj = _parse_json(llm_response.content)
            answer = normalize_label(obj.get("answer"))
            cited = {str(x) for x in obj.get("citations", [])} & ids
        except Exception:
            answer, cited = Label.INVALID, set()
        verification = self.verifier.verify_claim(task.world, task.question, cited, answer)
        return _llm_result(
            task,
            self.method_name,
            answer,
            ids,
            1,
            verification.valid and answer in {Label.TRUE, Label.FALSE},
            Counter({"RETRIEVE": 1, "STOP": 1}),
            llm_response.prompt_tokens,
            llm_response.completion_tokens,
            llm_response.latency_ms,
            llm_response.cost_usd,
        )


class ReActLLMBaseline:
    method_name = "react_llm"

    def __init__(self, client: LLMClient, max_steps: int = 12, top_k: int = 8) -> None:
        self.client = client
        self.max_steps = max_steps
        self.top_k = top_k
        self.verifier = ProofVerifier()

    def run(self, task: Task) -> RunResult:
        retriever = LocalRetriever(task.world)
        evidence = {}
        transcript = []
        calls = 0
        answer = Label.INVALID
        cited: set[str] = set()
        prompt_tokens = 0
        completion_tokens = 0
        latency_ms = 0.0
        cost_usd = 0.0

        for _ in range(self.max_steps):
            prompt = f"""You are a ReAct agent for an open-world logic task.
Question: {task.question.text}

Prior tool transcript:
{json.dumps(transcript, ensure_ascii=False)}

Return one JSON action:
{{"action":"SEARCH|ANSWER", "type":"fact|rule", "predicate":"...", "polarity":"+|-", "query":"...", "answer":"true|false|unknown", "citations":["id"]}}
"""
            response = self.client.complete([{"role": "user", "content": prompt}])
            prompt_tokens += response.prompt_tokens
            completion_tokens += response.completion_tokens
            latency_ms += response.latency_ms
            cost_usd += response.cost_usd
            try:
                action = _parse_json(response.content)
            except Exception:
                break
            if str(action.get("action", "")).upper() == "ANSWER":
                answer = normalize_label(action.get("answer"))
                cited = {str(x) for x in action.get("citations", [])} & set(evidence)
                break
            try:
                from ..schemas import EvidenceType, RetrievalRequest

                request = RetrievalRequest(
                    EvidenceType(str(action.get("type", "fact")).lower()),
                    str(action["predicate"]),
                    str(action.get("polarity", "+")),
                    query=str(action.get("query", task.question.text)),
                    top_k=self.top_k,
                )
            except Exception:
                break
            result = retriever.search(request)
            calls += 1
            for item in result.items:
                evidence[item.evidence_id] = item
            transcript.append(
                {
                    "request": request.key,
                    "results": [{"id": x.evidence_id, "text": x.text} for x in result.items],
                }
            )

        verification = self.verifier.verify_claim(task.world, task.question, cited, answer)
        return _llm_result(
            task,
            self.method_name,
            answer,
            set(evidence),
            calls,
            verification.valid and answer in {Label.TRUE, Label.FALSE},
            Counter({"RETRIEVE": calls, "STOP": 1}),
            prompt_tokens,
            completion_tokens,
            latency_ms,
            cost_usd,
        )
