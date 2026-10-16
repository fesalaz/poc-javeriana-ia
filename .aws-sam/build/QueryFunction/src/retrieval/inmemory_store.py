"""
Implementación en memoria de VectorStorePort — usada para verificar la lógica de
recuperación (filtro por curso, topK, umbral de similitud) sin necesitar una base de
datos real. En producción se reemplaza por PGVectorStore (PostgreSQL + pgvector +
índice HNSW), ver pgvector_store.py e Iteración 5 del diseño.
"""
import math
from typing import Any, Dict, List, Optional

from .store_base import VectorStorePort


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore(VectorStorePort):
    def __init__(self):
        self._rows: List[Dict[str, Any]] = []

    def add(self, chunk_id, text, embedding, course_id, metadata=None):
        self._rows.append({
            "chunk_id": chunk_id,
            "text": text,
            "embedding": embedding,
            "course_id": course_id,
            "metadata": metadata or {},
        })

    def search(self, query_embedding, course_id, top_k=5, score_threshold=0.0):
        scored = []
        for row in self._rows:
            if row["course_id"] != course_id:
                continue
            score = _cosine_similarity(query_embedding, row["embedding"])
            if score >= score_threshold:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"chunk_id": r["chunk_id"], "text": r["text"], "score": s, "metadata": r["metadata"]}
            for s, r in scored[:top_k]
        ]
