"""
Adaptador REAL para un tercer proveedor del Provider Registry (QAS-D1, QAS-I1):
el modelo de pesos abiertos `gpt-oss-20b` de OpenAI, servido por Groq en su
tier gratuito a través de un endpoint compatible con la API de OpenAI. Implementa
LLMProviderPort (Iteración 4). Se usa LangChain para mantener la restricción C1.

NOTA (agosto 2026): este adaptador es distinto del histórico "Llama vía Groq"
que existía antes en este proyecto. Groq deprecó `llama-3.1-8b-instant` el
16/08/2026 y sugirió como reemplazo `openai/gpt-oss-20b` — un modelo de OpenAI,
no de Meta. En vez de seguir llamando "Llama" a un modelo que no lo es, este
adaptador se separó en su propia clase (`OpenAIAdapter`), y `llama_adapter.py`
ahora apunta a un proveedor distinto (OpenRouter) que sí sirve un Llama real
(3.3, tier gratuito). Ver GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 0.1, y la nota
de la restricción C2 en arquitectura-add30-javeriana.md.

Requiere (en tu entorno):
    pip install langchain-openai
    export OPENAI_OSS_BASE_URL=https://api.groq.com/openai/v1
    export OPENAI_OSS_API_KEY=...   (clave gratuita de Groq: https://console.groq.com)
    export OPENAI_OSS_MODEL=openai/gpt-oss-20b

Este módulo NO se ejecuta en el entorno de verificación de este proyecto — se prueba
con MockLLMAdapter. Groq no expone /embeddings para este modelo; usa
GeminiAdapter.embed() (ya es agnóstico gracias al Provider Registry).

NOTA (agosto 2026 — timeouts en cadena): este es el proveedor de respaldo 1 en
la cadena de fallback. Igual que en gemini_adapter.py, se fija `timeout`
(`LLM_REQUEST_TIMEOUT_SECONDS`, default 6 s) y `max_retries=1` como defensa
adicional, pero el mecanismo que realmente garantiza el corte es el timeout
de pared de `ProviderRegistry._dispatch()` — se confirmó en CloudWatch Logs
que el `timeout=` de otro adaptador (Gemini) no se respetó en la práctica, y
no hay garantía de que el cliente OpenAI-compatible de este adaptador sea
distinto, así que no se confía únicamente en él. Ver
GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 0.2.
"""
import os
import time
from typing import List

from .base import GenerationResult, HealthStatus, LLMProviderPort

try:
    from langchain_openai import ChatOpenAI
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class OpenAIAdapter(LLMProviderPort):
    name = "openai"

    def __init__(self, model: str = None, base_url: str = None, api_key: str = None):
        if not _AVAILABLE:
            raise ImportError(
                "langchain-openai no está instalado. Ejecuta: pip install langchain-openai"
            )
        self._model_name = model or os.getenv("OPENAI_OSS_MODEL", "openai/gpt-oss-20b")
        self._base_url = base_url or os.getenv("OPENAI_OSS_BASE_URL", "https://api.groq.com/openai/v1")
        self._api_key = api_key or os.getenv("OPENAI_OSS_API_KEY")
        self._request_timeout = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "6"))
        self._llm = ChatOpenAI(
            model=self._model_name, base_url=self._base_url, api_key=self._api_key,
            timeout=self._request_timeout, max_retries=1,
        )

    def generate(self, prompt: str, **params) -> GenerationResult:
        start = time.monotonic()
        response = self._llm.invoke(prompt, **params)
        latency_ms = (time.monotonic() - start) * 1000
        return GenerationResult(text=response.content, provider=self.name, latency_ms=latency_ms)

    def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError(
            "Este endpoint (gpt-oss-20b vía Groq) no expone /embeddings; usa "
            "GeminiAdapter.embed() u otro modelo de embeddings (el Provider Registry "
            "ya soporta esta agnosticidad)."
        )

    def health_check(self) -> HealthStatus:
        try:
            self._llm.invoke("ping")
            return HealthStatus(healthy=True)
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(healthy=False, detail=str(exc))
