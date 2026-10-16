"""
Pruebas end-to-end del Orquestador con adaptadores simulados — verifican que el
diseño de las Iteraciones 1 y 2 funciona como está documentado: ruta CAG, ruta RAG,
cache-hit semántico, fallback entre proveedores (QAS-D1) y backpressure (QAS-P3).
"""
import unittest

from src.adapters.mock_adapter import MockLLMAdapter
from src.cache.semantic_cache import SemanticResponseCache
from src.cag.cache_store import CAGStore
from src.orchestrator.context_assembler import ContextAssembler
from src.orchestrator.orchestrator import Orchestrator, QueueFullError
from src.orchestrator.router import Router
from src.registry.circuit_breaker import CircuitBreaker
from src.registry.provider_registry import ProviderRegistry
from src.retrieval.inmemory_store import InMemoryVectorStore


def build_orchestrator(gemini_healthy=True, llama_healthy=True, max_in_flight=50):
    registry = ProviderRegistry()
    gemini = MockLLMAdapter(name="gemini", healthy=gemini_healthy)
    llama = MockLLMAdapter(name="llama", healthy=llama_healthy)
    registry.register(gemini, priority=1, breaker=CircuitBreaker(failure_threshold=3))
    registry.register(llama, priority=2, breaker=CircuitBreaker())

    vector_store = InMemoryVectorStore()
    cag_store = CAGStore()
    orchestrator = Orchestrator(
        provider_registry=registry,
        vector_store=vector_store,
        cag_store=cag_store,
        semantic_cache=SemanticResponseCache(),
        context_assembler=ContextAssembler(),
        router=Router(cag_store),
        top_k=3,
        score_threshold=0.0,
        max_in_flight=max_in_flight,
    )
    return orchestrator, registry, vector_store, cag_store, gemini, llama


class TestOrchestratorEndToEnd(unittest.TestCase):
    def test_uses_rag_when_no_cag_preloaded(self):
        orch, registry, store, cag, *_ = build_orchestrator()
        store.add("c1", "contenido relevante del curso", [1.0, 0.0], course_id="A")
        # el mock embed() es determinista por hash, así que forzamos un chunk fácil
        # de encontrar usando score_threshold=0.0 (ver setup del orquestador)
        result = orch.handle_query("est-1", "A", "pregunta cualquiera")
        self.assertFalse(result["used_cag"])
        self.assertTrue(result["source"].startswith("llm:"))

    def test_uses_cag_when_preloaded(self):
        orch, registry, store, cag, *_ = build_orchestrator()
        cag.preload("B", "contexto precargado del curso B")
        result = orch.handle_query("est-1", "B", "pregunta cualquiera")
        self.assertTrue(result["used_cag"])

    def test_semantic_cache_hit_on_repeated_question(self):
        orch, *_ = build_orchestrator()
        q = "¿cuál es la fecha del examen final?"
        first = orch.handle_query("est-1", "A", q)
        second = orch.handle_query("est-2", "A", q)
        self.assertNotEqual(first["source"], "semantic_cache")
        self.assertEqual(second["source"], "semantic_cache")
        self.assertEqual(second["response"], first["response"])

    def test_fallback_when_primary_provider_down(self):
        orch, *_ = build_orchestrator(gemini_healthy=False, llama_healthy=True)
        result = orch.handle_query("est-1", "A", "pregunta única sin caché")
        self.assertEqual(result["source"], "llm:llama")

    def test_queue_full_raises_qas_p3(self):
        orch, *_ = build_orchestrator(max_in_flight=1)
        orch._in_flight = 1  # simula saturación (ver QAS-P3, Iteración 2)
        with self.assertRaises(QueueFullError):
            orch.handle_query("est-1", "A", "pregunta cuando la cola está llena")


if __name__ == "__main__":
    unittest.main()
