"""Policy-clause retrieval.

Primary backend is Qdrant (``QDRANT_URL``). If Qdrant is not reachable —
the common case on a laptop without Docker — the store falls back to a
deterministic in-memory vector search (hash-based bag-of-words vectors
with cosine similarity), so the app always runs. The retrieval agent
uses the same ``search()`` interface either way.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DIM = 128

_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
)


@contextmanager
def _no_proxy_env():
    """Temporarily hide proxy env vars.

    The sandbox sets an egress proxy whose NO_PROXY list contains
    bracketed IPv6 entries that httpx (used by qdrant-client) cannot
    parse, so even localhost Qdrant connections fail with
    ``InvalidURL: Invalid port``. Qdrant is always a local/docker
    service that must bypass the proxy, so we hide these vars only
    while the client is constructed (httpx reads them at that point)
    and restore them immediately afterwards.
    """
    saved = {k: os.environ.pop(k) for k in _PROXY_ENV_KEYS if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _embed(text: str) -> list[float]:
    """Deterministic bag-of-words embedding (no model call needed)."""
    vec = [0.0] * _DIM
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % _DIM
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class ClauseHit:
    clause: dict
    score: float


class InMemoryVectorStore:
    """Keyword + vector hybrid over the local clause file."""

    def __init__(self, clauses: list[dict]) -> None:
        self._clauses = clauses
        self._vectors = [
            _embed(c["title"] + " " + c["text"] + " " + " ".join(c.get("keywords", [])))
            for c in clauses
        ]

    def search(self, query: str, top_k: int = 3) -> list[ClauseHit]:
        q = _embed(query)
        scored: list[ClauseHit] = []
        set(re.findall(r"[a-z0-9]+", query.lower()))
        for clause, vec in zip(self._clauses, self._vectors):
            score = _cosine(q, vec)
            # Small keyword bonus keeps exact product terms on top.
            kw_hits = sum(
                1 for kw in clause.get("keywords", []) if kw.lower() in query.lower()
            )
            score += 0.05 * kw_hits
            scored.append(ClauseHit(clause=clause, score=round(score, 4)))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]


class QdrantVectorStore:
    """Qdrant-backed retrieval (used when a Qdrant server is reachable)."""

    def __init__(self, url: str, collection: str, clauses: list[dict]) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        with _no_proxy_env():
            self._client = QdrantClient(url=url, timeout=5)
        self._collection = collection
        self._clauses = {c["id"]: c for c in clauses}
        self._client.recreate_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=_DIM, distance=Distance.COSINE),
        )
        points = [
            PointStruct(
                id=i,
                vector=_embed(
                    c["title"] + " " + c["text"] + " " + " ".join(c.get("keywords", []))
                ),
                payload={"clause_id": c["id"]},
            )
            for i, c in enumerate(clauses)
        ]
        self._client.upsert(collection_name=collection, points=points)

    def search(self, query: str, top_k: int = 3) -> list[ClauseHit]:
        results = self._client.query_points(
            collection_name=self._collection,
            query=_embed(query),
            limit=top_k,
        ).points
        return [
            ClauseHit(
                clause=self._clauses[r.payload["clause_id"]],
                score=round(r.score, 4),
            )
            for r in results
        ]


def load_clauses(data_dir: str | None = None) -> list[dict]:
    base = data_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    path = os.path.join(base, "policy_clauses.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_vector_store(data_dir: str | None = None):
    """Return a vector store, preferring Qdrant, falling back in-memory."""
    from app.config import settings

    clauses = load_clauses(data_dir)
    try:
        store = QdrantVectorStore(
            settings.qdrant_url, settings.qdrant_collection, clauses
        )
        logger.info("Vector store: Qdrant at %s", settings.qdrant_url)
        return store
    except Exception as exc:  # noqa: BLE001 — any failure means "no Qdrant"
        logger.warning(
            "Qdrant unreachable at %s (%s) — using in-memory vector store.",
            settings.qdrant_url,
            exc,
        )
        return InMemoryVectorStore(clauses)
