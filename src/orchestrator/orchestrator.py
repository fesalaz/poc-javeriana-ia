"""
Orquestador RAG/CAG — corazón de la arquitectura (Iteraciones 1 y 2 del diseño
ADD 3.0). Integra: cola acotada con backpressure (QAS-P3), caché semántico
(QAS-P1/P3), recuperación RAG + CAG en paralelo (QAS-P1), ensamblado de contexto,
invocación al LLM vía Provider Registry con fallback (QAS-D1, QAS-I1).
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional


class QueueFullError(Exception):
    """Se lanza cuando se excede la capacidad configurada (QAS-P3): en producción,
    el Gateway debe responder 503 + Retry-After o degradar hacia el caché semántico."""


class Orchestrator:
    def __init__(self, provider_registry, vector_store, cag_store, semantic_cache,
                 context_assembler, router=None, embedding_provider=None,
                 top_k: int = 5, score_threshold: float = 0.2, max_in_flight: int = 50,
                 activity_logger=None):
        self.registry = provider_registry
        self.vector_store = vector_store
        self.cag_store = cag_store
        self.semantic_cache = semantic_cache
        self.assembler = context_assembler
        self.router = router
        self.embedding_provider = embedding_provider or provider_registry
        self.top_k = top_k
        self.score_threshold = score_threshold
        self.max_in_flight = max_in_flight
        self._in_flight = 0
        # Activity Logger opcional (Iteración 6): si se provee, cada consulta
        # atendida se registra como evento (sin el texto de la conversación,
        # solo metadatos) para alimentar el seguimiento de avance (FR6).
        self.activity_logger = activity_logger

    def handle_query(self, student_id: str, course_id: str, message: str) -> dict:
        if self._in_flight >= self.max_in_flight:
            raise QueueFullError(
                "cola llena (QAS-P3): degradar la respuesta o reintentar más tarde"
            )
        self._in_flight += 1
        start = time.monotonic()
        try:
            query_embedding = self.embedding_provider.embed([message])[0]

            cached_response = self.semantic_cache.get(course_id, query_embedding)
            if cached_response is not None:
                result_payload = {
                    "response": cached_response,
                    "source": "semantic_cache",
                    "used_cag": None,
                    "rag_chunks": 0,
                    "latency_ms": (time.monotonic() - start) * 1000,
                }
                self._log_query(student_id, course_id, result_payload)
                return result_payload

            cag_context: Optional[str] = None
            rag_chunks = []
            with ThreadPoolExecutor(max_workers=2) as pool:
                cag_future = pool.submit(self.cag_store.get, course_id)
                rag_future = pool.submit(
                    self.vector_store.search, query_embedding, course_id,
                    self.top_k, self.score_threshold,
                )
                cag_context = cag_future.result()
                rag_chunks = rag_future.result()

            prompt = self.assembler.assemble(message, cag_context=cag_context, rag_chunks=rag_chunks)
            result = self.registry.generate(prompt)

            self.semantic_cache.put(course_id, query_embedding, result.text)

            result_payload = {
                "response": result.text,
                "source": f"llm:{result.provider}",
                "used_cag": cag_context is not None,
                "rag_chunks": len(rag_chunks),
                "latency_ms": (time.monotonic() - start) * 1000,
            }
            self._log_query(student_id, course_id, result_payload)
            return result_payload
        finally:
            self._in_flight -= 1

    def _log_query(self, student_id: str, course_id: str, result_payload: dict) -> None:
        if not self.activity_logger:
            return
        self.activity_logger.log_event(
            student_id, course_id, "query",
            {
                "source": result_payload["source"],
                "latency_ms": result_payload["latency_ms"],
                "used_cag": result_payload["used_cag"],
            },
        )
