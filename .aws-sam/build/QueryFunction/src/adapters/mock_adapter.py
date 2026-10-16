"""
Adaptador simulado (mock) de LLMProviderPort. No requiere credenciales ni red.
Se usa para verificar la lógica del Orquestador/Provider Registry/Circuit Breaker
sin depender de Gemini/Llama 3.1 reales (ver README, sección "Cómo se verificó").

Permite simular fallas (fail_rate) o marcar el proveedor como caído (set_healthy)
para probar el circuit breaker y el fallback entre proveedores (QAS-D1).
"""
import hashlib
import random
import time
from typing import List

from .base import GenerationResult, HealthStatus, LLMProviderPort


class MockLLMAdapter(LLMProviderPort):
    def __init__(self, name: str = "mock", fail_rate: float = 0.0,
                 latency_s: float = 0.0, healthy: bool = True, supports_embed: bool = True,
                 supports_generate: bool = True):
        self.name = name
        self.fail_rate = fail_rate
        self.latency_s = latency_s
        self._healthy = healthy
        # Simula proveedores reales que no exponen /embeddings (p.ej. Groq/
        # OpenRouter en este proyecto): embed() debe fallar con
        # NotImplementedError, no con una excepción genérica, para que
        # ProviderRegistry lo trate como "no soportado" y no como falla del
        # circuit breaker (ver src/registry/provider_registry.py).
        self.supports_embed = supports_embed
        # Simula proveedores que solo hacen embeddings (AWS Bedrock, Voyage
        # AI — respaldos de embed() agregados en septiembre 2026, ver
        # bedrock_embedding_adapter.py/voyage_adapter.py): generate() debe
        # fallar con NotImplementedError, por la misma razón que arriba.
        self.supports_generate = supports_generate
        self.call_count = 0
        self.embed_call_count = 0

    def generate(self, prompt: str, **params) -> GenerationResult:
        if not self.supports_generate:
            raise NotImplementedError(f"[{self.name}] no ofrece generate() (simulado)")
        self.call_count += 1
        if self.latency_s:
            time.sleep(self.latency_s)
        if not self._healthy or random.random() < self.fail_rate:
            raise ConnectionError(f"[{self.name}] fallo simulado del proveedor")
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        return GenerationResult(
            text=f"[respuesta simulada de {self.name} #{digest}] {prompt[-120:]}",
            provider=self.name,
            latency_ms=self.latency_s * 1000,
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self.supports_embed:
            raise NotImplementedError(f"[{self.name}] no expone /embeddings (simulado)")
        self.embed_call_count += 1
        if not self._healthy:
            raise ConnectionError(f"[{self.name}] fallo simulado en embeddings")
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([b / 255.0 for b in digest[:16]])
        return vectors

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=self._healthy, detail=f"mock:{self.name}")

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy
