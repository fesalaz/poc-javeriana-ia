"""
Contrato formal LLMProviderPort (v1.0) — definido en la Iteración 4 del diseño ADD 3.0.
Cualquier proveedor LLM debe implementar esta interfaz para integrarse a la arquitectura
sin modificar el Orquestador (satisface QAS-I1). El Provider Registry (ver
src/registry/provider_registry.py) resuelve en tiempo de ejecución qué adaptador usar.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class GenerationResult:
    text: str
    provider: str
    latency_ms: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthStatus:
    healthy: bool
    detail: str = ""


class LLMProviderPort(ABC):
    """Puerto (interfaz) que deben implementar todos los adaptadores de proveedor LLM."""

    name: str = "unset"

    @abstractmethod
    def generate(self, prompt: str, **params) -> GenerationResult:
        """Genera una respuesta a partir de un prompt ya ensamblado."""

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Calcula embeddings para una lista de textos."""

    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Usado por el circuit breaker / reintroducción (QAS-D1)."""
