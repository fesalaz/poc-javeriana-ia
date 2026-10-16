"""
Adaptador REAL para Google AI Studio (Gemini), vía LangChain (restricción C1 del diseño).
Implementa LLMProviderPort (Iteración 4).

Requiere (en tu entorno, no en el sandbox de este chat):
    pip install langchain-google-genai
    export GOOGLE_API_KEY=...   (clave gratuita de Google AI Studio)

Este módulo NO se ejecuta en el entorno de verificación de este proyecto (no hay
acceso a internet ni a langchain instalado allí) — se prueba con MockLLMAdapter.
Actívalo en tu máquina/AWS Lambda instalando la dependencia anterior.

NOTA (agosto 2026): el modelo de embeddings `models/text-embedding-004` se
apagó el 14/01/2026; el reemplazo estable es `gemini-embedding-001`. Es
configurable con la variable de entorno `GEMINI_EMBEDDING_MODEL` (ver
`.env.example`) para no repetir el problema de hardcodear un modelo que luego
se deprecia — ver GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 0.1.

NOTA (agosto 2026 — timeouts en cadena): este adaptador es el primero (prioridad
1) en la cadena de fallback del Provider Registry (Gemini -> OpenAI/gpt-oss-20b
-> Llama 3.3). Sin un tope explícito, un proveedor que se queda "colgado" (red
lenta, throttling) puede consumir todo el Timeout de la función Lambda antes de
que el circuit breaker tenga oportunidad de conmutar al siguiente proveedor —
causa raíz de los ReadTimeout observados en scripts/smoke_test_deployed_api.py.
Por eso se fija `timeout` (por-llamada) vía `LLM_REQUEST_TIMEOUT_SECONDS`
(default 6 s) y `max_retries=1` (para no apilar reintentos de LangChain encima
del propio fallback del Provider Registry).

CONFIRMADO EN PRODUCCIÓN (agosto 2026): este `timeout=` NO se respetó en la
práctica — CloudWatch Logs del PoC desplegado mostró 3 invocaciones seguidas
con `Status: timeout` y `Duration: 28000.00 ms` exactos (el Timeout completo
de la función Lambda), es decir, la llamada a Gemini se quedó colgada mucho
más de 6-8s sin que este parámetro la cortara. Coincide con un issue abierto
en langchain-google-genai (github.com/langchain-ai/langchain-google/issues/731).
Por eso el respaldo real ya no es este parámetro: `ProviderRegistry` (ver
`src/registry/provider_registry.py`) ejecuta cada llamada en un hilo aparte y
deja de esperarla tras `LLM_REQUEST_TIMEOUT_SECONDS`, sin depender de que el
SDK del proveedor coopere. El `timeout=` de aquí queda como defensa adicional
(por si una versión futura de la librería sí lo respeta), no como el
mecanismo principal.

SEGUNDO HALLAZGO (agosto 2026, `scripts/smoke_test_real_providers.py` en
local, antes de desplegar): al bajar `LLM_REQUEST_TIMEOUT_SECONDS` de 8 a 6
para dejar margen en el presupuesto de Lambda (ver nota anterior), Gemini
empezó a fallar de inmediato con `400 INVALID_ARGUMENT: "Manually set
deadline 6s is too short. Minimum allowed deadline is 10s."` — la propia API
de Google **rechaza** cualquier deadline menor a 10s, no solo lo ignora. Es
decir, el `timeout=` de este adaptador tiene un piso duro de 10s que no
depende de LangChain sino de la API de Google, y es distinto del timeout de
PARED de `ProviderRegistry` (que sí puede ser menor, porque no depende de
este parámetro — solo deja de esperar el hilo). Por eso aquí se usa
`max(LLM_REQUEST_TIMEOUT_SECONDS, 10.0)` para el parámetro `timeout=` del
SDK de Gemini específicamente, mientras que `ProviderRegistry` sigue usando
el valor sin recortar (6s por defecto) para decidir cuándo dejar de esperar
a este adaptador — son dos mecanismos independientes y compatibles entre sí.
Ver GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 0.2.
"""
import os
import time
from typing import List

from .base import GenerationResult, HealthStatus, LLMProviderPort

try:
    from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class GeminiAdapter(LLMProviderPort):
    name = "gemini"

    def __init__(self, model: str = None, api_key: str = None):
        if not _AVAILABLE:
            raise ImportError(
                "langchain-google-genai no está instalado. Ejecuta: "
                "pip install langchain-google-genai"
            )
        self._model_name = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        self._embedding_model_name = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
        self._api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self._request_timeout = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "6"))
        # Piso duro de la API de Google: un deadline menor a 10s se rechaza
        # con 400 INVALID_ARGUMENT antes de intentar la llamada (ver nota de
        # módulo). Solo afecta el parámetro que le pasamos al SDK de Gemini;
        # el timeout de PARED de ProviderRegistry sigue usando
        # self._request_timeout sin recortar (puede seguir siendo <10s).
        self._sdk_timeout = max(self._request_timeout, 10.0)
        self._llm = ChatGoogleGenerativeAI(
            model=self._model_name, google_api_key=self._api_key,
            timeout=self._sdk_timeout, max_retries=1,
        )
        self._embeddings = GoogleGenerativeAIEmbeddings(
            model=self._embedding_model_name, google_api_key=self._api_key,
            request_options={"timeout": self._sdk_timeout},
        )

    def generate(self, prompt: str, **params) -> GenerationResult:
        start = time.monotonic()
        response = self._llm.invoke(prompt, **params)
        latency_ms = (time.monotonic() - start) * 1000
        return GenerationResult(text=response.content, provider=self.name, latency_ms=latency_ms)

    def embed(self, texts: List[str]) -> List[List[float]]:
        return self._embeddings.embed_documents(texts)

    def health_check(self) -> HealthStatus:
        try:
            self._llm.invoke("ping")
            return HealthStatus(healthy=True)
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(healthy=False, detail=str(exc))
