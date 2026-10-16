"""
Adaptador REAL de embeddings sobre Voyage AI (voyage-4-lite). Implementa
LLMProviderPort (Iteración 4), pero SOLO para `embed()` — Voyage AI no ofrece
modelos de generación de texto, así que `generate()` no está implementado a
propósito (no es una omisión, es una limitación real del proveedor).

Es el respaldo 2 de `embed()`, después de Gemini (primario) y AWS Bedrock/
Titan Embeddings V2 (respaldo 1) — ver `bedrock_embedding_adapter.py` para el
contexto completo de por qué se agregó esta redundancia (hallazgo R-7 de
`EVALUACION_ATAM_LITE.md`). Se eligió Voyage AI como segundo respaldo, y no
solo Bedrock, por diversidad real de proveedor: si Bedrock tuviera una
degradación propia (de servicio o de cuota), Voyage es una infraestructura
completamente independiente de AWS. Precio: $0.02 por 1M tokens de entrada,
igual que Bedrock, con 200 millones de tokens gratis por cuenta (cubre de
sobra cualquier prueba de carga de esta PoC).

Requiere (en tu entorno):
    pip install requests
    export VOYAGE_API_KEY=...   (https://dash.voyageai.com/)
    export VOYAGE_MODEL=voyage-4-lite   (default)

Este módulo NO se ejecuta en el entorno de verificación de este proyecto (sin
credenciales reales ni acceso a internet) — se prueba a nivel de
ProviderRegistry con MockLLMAdapter (ver tests/test_provider_registry.py).
"""
import os
import time
from typing import List

from .base import GenerationResult, HealthStatus, LLMProviderPort

try:
    import requests
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class VoyageAdapter(LLMProviderPort):
    """Respaldo 2 de `embed()` (ver módulo). `generate()` no está implementado
    porque Voyage AI no ofrece modelos de generación de texto."""

    name = "voyage"

    def __init__(self, model: str = None, base_url: str = None, api_key: str = None):
        if not _AVAILABLE:
            raise ImportError("requests no está instalado. Ejecuta: pip install requests")
        self._model = model or os.getenv("VOYAGE_MODEL", "voyage-4-lite")
        self._base_url = base_url or os.getenv("VOYAGE_BASE_URL", "https://api.voyageai.com/v1")
        self._api_key = api_key or os.getenv("VOYAGE_API_KEY")
        self._request_timeout = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "6"))

    def generate(self, prompt: str, **params) -> GenerationResult:
        raise NotImplementedError(
            "Voyage AI no ofrece modelos de generación de texto — solo embeddings y "
            "reranking. Este adaptador solo implementa embed()."
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        response = requests.post(
            f"{self._base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"input": texts, "model": self._model},
            timeout=self._request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        # La API devuelve los embeddings en el mismo orden que "input".
        return [item["embedding"] for item in payload["data"]]

    def health_check(self) -> HealthStatus:
        try:
            self.embed(["ping"])
            return HealthStatus(healthy=True)
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(healthy=False, detail=str(exc))
