"""
Adaptador REAL para Meta Llama (tercer proveedor del Provider Registry, QAS-D1,
QAS-I1), vía OpenRouter, usando un endpoint compatible con la API de OpenAI.
Implementa LLMProviderPort (Iteración 4). Se usa LangChain para mantener la
restricción C1.

NOTA (agosto 2026 — historial completo, ver `GUIA_DESPLIEGUE_Y_PRUEBAS.md`
sección 0.1 para el detalle): este adaptador apuntaba originalmente a Groq
para servir Llama 3.1 (`llama-3.1-8b-instant`), tal como exigía la
restricción C2 original del diseño de arquitectura ("Meta Llama 3.1"). Groq
deprecó ese modelo el 16/08/2026 y no ofrece ningún Llama gratuito como
reemplazo directo (solo `openai/gpt-oss-20b`, que no es un modelo Llama —
ver `openai_adapter.py`). Se intentó `meta-llama/llama-3.3-70b-instruct:free`
en OpenRouter, que primero falló por permisos de datos (Settings → Privacy),
y tras habilitarlos falló de nuevo con un 404 distinto y explícito:
`"This model is unavailable for free. The paid version is available now -
use this slug instead: meta-llama/llama-3.3-70b-instruct"`. Es decir: el
slug `:free` de este modelo específico dejó de estar disponible en la
práctica (OpenRouter retira/rota qué modelos ofrece gratis con frecuencia,
incluso cuando su página de catálogo todavía lo muestra como "Free" — no es
algo que se arregle con configuración de la cuenta).

Por eso el adaptador usa como default la variante de **pago**
`meta-llama/llama-3.3-70b-instruct` (sin `:free`) — sigue siendo Llama 3.3
real de Meta (desviación de versión ya documentada frente a Llama 3.1 en
C2), pero deja de ser "tier gratuito": es una **segunda desviación**
documentada de las restricciones C2/C5, con costo real aunque pequeño para
un PoC (~USD 0.10/1M tokens de entrada, USD 0.32/1M de salida, proveedor más
barato). Si en el futuro OpenRouter vuelve a ofrecer un slug `:free`
funcional para Llama 3.3 (o cualquier otro Llama), basta cambiar
`LLAMA_MODEL` — es solo configuración (QAS-I1), no requiere tocar este
adaptador ni el Orquestador.

Requiere (en tu entorno):
    pip install langchain-openai
    export LLAMA_BASE_URL=https://openrouter.ai/api/v1
    export LLAMA_API_KEY=...   (clave de https://openrouter.ai/; el modelo por
                                 defecto es de pago, requiere créditos cargados
                                 en Settings -> Credits)
    export LLAMA_MODEL=meta-llama/llama-3.3-70b-instruct

Este módulo NO se ejecuta en el entorno de verificación de este proyecto — se prueba
con MockLLMAdapter. OpenRouter no garantiza /embeddings para este modelo; usa
GeminiAdapter.embed() (ya es agnóstico gracias al Provider Registry).

NOTA (agosto 2026 — timeouts en cadena): este es el proveedor de respaldo 2
(último de la cadena). Igual que los otros dos adaptadores, se fija `timeout`
(`LLM_REQUEST_TIMEOUT_SECONDS`, default 6 s) y `max_retries=1` como defensa
adicional. El corte real y garantizado, si este proveedor también se cuelga,
lo aplica `ProviderRegistry._dispatch()` con un timeout de pared por hilo —
no se confía en que el `timeout=` del cliente lo respete (se confirmó en
producción que `ChatGoogleGenerativeAI` no lo hizo). Así el Orquestador puede
responder con degradación controlada (QAS-P3) en vez de dejar la petición
colgada hasta que expire el `Timeout` completo de la función Lambda. Ver
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


class LlamaAdapter(LLMProviderPort):
    name = "llama"

    def __init__(self, model: str = None, base_url: str = None, api_key: str = None):
        if not _AVAILABLE:
            raise ImportError(
                "langchain-openai no está instalado. Ejecuta: pip install langchain-openai"
            )
        self._model_name = model or os.getenv("LLAMA_MODEL", "meta-llama/llama-3.3-70b-instruct")
        self._base_url = base_url or os.getenv("LLAMA_BASE_URL", "https://openrouter.ai/api/v1")
        self._api_key = api_key or os.getenv("LLAMA_API_KEY")
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
            "Este endpoint de Llama 3.3 (OpenRouter) no expone /embeddings de forma "
            "confiable en el tier gratuito; usa GeminiAdapter.embed() u otro modelo "
            "de embeddings (el Provider Registry ya soporta esta agnosticidad)."
        )

    def health_check(self) -> HealthStatus:
        try:
            self._llm.invoke("ping")
            return HealthStatus(healthy=True)
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(healthy=False, detail=str(exc))
