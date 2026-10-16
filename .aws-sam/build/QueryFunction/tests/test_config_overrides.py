"""Verifica que build_orchestrator() acepte registry/cag_store inyectados
(usado por lambda_handler.py para conectar PGVectorStore/DynamoCAGStore reales
en AWS sin duplicar el ensamblaje). Iteración 3/5, adaptación a producción."""
import unittest

from src.adapters.mock_adapter import MockLLMAdapter
from src.cag.cache_store import CAGStore
from src.config import build_orchestrator
from src.registry.circuit_breaker import CircuitBreaker
from src.registry.provider_registry import ProviderRegistry


class TestConfigOverrides(unittest.TestCase):
    def test_injected_registry_and_cag_store_are_used(self):
        custom_registry = ProviderRegistry()
        custom_registry.register(MockLLMAdapter(name="proveedor-custom"), priority=1,
                                  breaker=CircuitBreaker())
        custom_cag_store = CAGStore()
        custom_cag_store.preload("curso-X", "contexto inyectado")

        orchestrator = build_orchestrator(registry=custom_registry, cag_store=custom_cag_store)

        self.assertIs(orchestrator.registry, custom_registry)
        self.assertIs(orchestrator.cag_store, custom_cag_store)
        result = orchestrator.handle_query("est-1", "curso-X", "pregunta cualquiera")
        self.assertEqual(result["source"], "llm:proveedor-custom")
        self.assertTrue(result["used_cag"])

    def test_defaults_unchanged_when_not_overridden(self):
        orchestrator = build_orchestrator()
        # Comportamiento por defecto: mocks + almacenes en memoria (sin romper
        # lo ya construido en las iteraciones anteriores). Desde la incorporación
        # del tercer proveedor, el orden es gemini -> openai (gpt-oss-20b/Groq)
        # -> llama (3.3/OpenRouter).
        names = [name for name, _ in orchestrator.registry.list_providers()]
        self.assertEqual(names, ["gemini", "openai", "llama"])


if __name__ == "__main__":
    unittest.main()
