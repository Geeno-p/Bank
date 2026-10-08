"""Hybrid control retrieval: BM25 + dense (BGE) + topic bonus, fused with Reciprocal Rank Fusion."""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from rank_bm25 import BM25Okapi

from regintel.schemas.control import Control
from regintel.schemas.obligation import Obligation
from regintel.settings import get_settings

RRF_K = 60
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # BGE query instruction
STOP = {"the", "and", "for", "with", "any", "its", "their", "shall", "such", "be", "of", "to", "in",
        "or", "a", "an", "by", "on", "as", "is", "are", "at", "from", "that", "this", "which"}
EmbedFn = Callable[[list[str]], np.ndarray]


def tokens(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1 and w not in STOP]


@lru_cache
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(get_settings().embedding_model)


def default_embed(texts: list[str]) -> np.ndarray:
    return np.asarray(_model().encode(texts, normalize_embeddings=True, batch_size=32))


def control_text(c: Control) -> str:
    params = " ".join(f"{p.name} {p.value} {p.unit or ''}" for p in c.parameters)
    return " ".join([c.name, c.objective, c.description, c.source.quote, params])


def obligation_text(o: Obligation) -> str:
    return " ".join(x for x in (o.actor, o.action, o.condition, o.source.quote) if x)


def _ranks(scores: Sequence[float]) -> np.ndarray:
    order = np.argsort(-np.asarray(scores), kind="stable")
    r = np.empty(len(order), dtype=int)
    r[order] = np.arange(1, len(order) + 1)
    return r


@dataclass
class Candidate:
    control: Control
    score: float
    bm25_rank: int
    dense_rank: int


class ControlIndex:
    def __init__(self, controls: list[Control], embed: EmbedFn | None = None) -> None:
        self.controls = list(controls)
        self.embed = embed or default_embed
        docs = [control_text(c) for c in self.controls]
        self.bm25 = BM25Okapi([tokens(d) or ["_"] for d in docs])
        self.vecs = self.embed(docs)

    def search(self, o: Obligation, k: int = 6, topic_bonus: float = 0.5) -> list[Candidate]:
        q = obligation_text(o)
        bm_rank = _ranks(self.bm25.get_scores(tokens(q) or ["_"]))
        dn_rank = _ranks(self.vecs @ self.embed([QUERY_PREFIX + q])[0])
        topics = set(o.topics)
        out = []
        for i, c in enumerate(self.controls):
            s = 1 / (RRF_K + bm_rank[i]) + 1 / (RRF_K + dn_rank[i])
            if topics & set(c.topics):
                s += topic_bonus / RRF_K
            out.append(Candidate(c, round(s, 5), int(bm_rank[i]), int(dn_rank[i])))
        return sorted(out, key=lambda x: -x.score)[:k]