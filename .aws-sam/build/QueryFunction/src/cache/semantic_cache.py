"""
Caché semántico de respuestas (Iteración 2, táctica de desempeño para QAS-P1/QAS-P3):
si una pregunta es semánticamente equivalente a una ya respondida recientemente
dentro del mismo curso, se responde de inmediato sin volver a invocar recuperación
ni al LLM. Se apoya en los mismos embeddings usados para RAG (LLMProviderPort.embed()).
"""
import math
import time
from typing import List, Optional


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)


class SemanticResponseCache:
    def __init__(self, similarity_threshold: float = 0.95, ttl_seconds: float = 600,
                 clock=time.monotonic):
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries = []

    def _expire(self):
        now = self._clock()
        self._entries = [e for e in self._entries if now - e["ts"] <= self.ttl_seconds]

    def get(self, course_id: str, query_embedding: List[float]) -> Optional[str]:
        self._expire()
        for entry in self._entries:
            if entry["course_id"] != course_id:
                continue
            if _cosine_similarity(entry["embedding"], query_embedding) >= self.similarity_threshold:
                return entry["response"]
        return None

    def put(self, course_id: str, query_embedding: List[float], response: str) -> None:
        self._entries.append({
            "course_id": course_id,
            "embedding": query_embedding,
            "response": response,
            "ts": self._clock(),
        })

    def invalidate_course(self, course_id: str) -> None:
        self._entries = [e for e in self._entries if e["course_id"] != course_id]
