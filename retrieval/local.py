from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ..schemas import EvidenceItem, EvidenceType, RetrievalRequest, World


_WORD_RE = re.compile(r"[A-Za-z0-9_-]+")


def tokenize(text: str) -> list[str]:
    return [x.lower() for x in _WORD_RE.findall(text)]


@dataclass
class SearchResponse:
    items: list[EvidenceItem]
    scores: list[float]
    total_matches: int


class LocalRetriever:
    """Small, dependency-free BM25 retriever with structural filters."""

    def __init__(self, world: World, k1: float = 1.5, b: float = 0.75) -> None:
        self.world = world
        self.k1 = k1
        self.b = b
        self._items = list(world.evidence.values())
        self._tokens = {item.evidence_id: tokenize(item.text) for item in self._items}
        lengths = [len(x) for x in self._tokens.values()]
        self._avgdl = sum(lengths) / len(lengths) if lengths else 1.0
        self._df: Counter[str] = Counter()
        for tokens in self._tokens.values():
            self._df.update(set(tokens))

    def _bm25(self, query_tokens: list[str], item: EvidenceItem) -> float:
        tokens = self._tokens[item.evidence_id]
        counts = Counter(tokens)
        n_docs = max(1, len(self._items))
        score = 0.0
        for token in query_tokens:
            df = self._df.get(token, 0)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            tf = counts.get(token, 0)
            denom = tf + self.k1 * (1.0 - self.b + self.b * len(tokens) / self._avgdl)
            if denom:
                score += idf * (tf * (self.k1 + 1.0)) / denom
        return score

    def search(self, request: RetrievalRequest) -> SearchResponse:
        candidates: list[EvidenceItem] = []
        for item in self._items:
            if item.evidence_type != request.evidence_type:
                continue
            if item.signature != (request.predicate, request.polarity):
                continue
            candidates.append(item)

        query_tokens = tokenize(request.query)
        scored = [(self._bm25(query_tokens, item), item) for item in candidates]
        scored.sort(key=lambda pair: (-pair[0], pair[1].evidence_id))
        selected = scored[: request.top_k]
        return SearchResponse(
            items=[item for _, item in selected],
            scores=[score for score, _ in selected],
            total_matches=len(candidates),
        )

    def search_text(self, query: str, top_k: int = 8) -> SearchResponse:
        query_tokens = tokenize(query)
        scored = [(self._bm25(query_tokens, item), item) for item in self._items]
        scored.sort(key=lambda pair: (-pair[0], pair[1].evidence_id))
        selected = scored[:top_k]
        return SearchResponse(
            items=[item for _, item in selected],
            scores=[score for score, _ in selected],
            total_matches=len(self._items),
        )
