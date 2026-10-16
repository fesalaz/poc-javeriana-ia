"""
Adaptador REAL de embeddings sobre Amazon Bedrock (Titan Text Embeddings V2).
Implementa LLMProviderPort (Iteración 4), pero SOLO para `embed()` — no es un
proveedor de `generate()` (ver nota más abajo). Agregado en septiembre de 2026
para cerrar el hallazgo R-7 de `EVALUACION_ATAM_LITE.md`: de los 3 proveedores
originales del Provider Registry, solo Gemini implementaba `embed()`; bajo la
prueba de carga real de 100 usuarios concurrentes, el circuit breaker de
Gemini se abrió y `embed()` se quedó sin ningún proveedor alterno al que
conmutar — no por falla del propio circuit breaker (que funcionó como se
diseñó), sino porque nunca hubo redundancia funcional real para esa
operación.

Por qué Bedrock como respaldo 1 de `embed()` (no de `generate()`):
- Es el más económico de las opciones evaluadas ($0.02 por 1M tokens de
  entrada, Titan Text Embeddings V2) empatado con OpenAI/Voyage/Jina.
- Al estar en la misma nube (AWS) que el resto de la infraestructura, la
  autenticación es con el rol de ejecución de `QueryFunction` (IAM), sin
  agregar una credencial nueva a `LLMSecrets` ni una dependencia de red hacia
  un cuarto proveedor SaaS externo.
- Deliberadamente NO se usa como respaldo de `generate()`: la cadena de
  fallback de generación (Gemini -> OpenAI/Groq -> Llama/OpenRouter) ya está
  verificada empíricamente varias veces frente a cambios reales del
  ecosistema (ver NR-1 en `EVALUACION_ATAM_LITE.md`) y no mostró ningún
  problema — this adaptador no la toca, para no reemplazar algo que ya
  funciona por resolver algo que sí estaba roto (`embed()`).

Requiere (en tu entorno / rol de ejecución de Lambda):
    - Permiso IAM `bedrock:InvokeModel` sobre el modelo de embeddings (ver
      `infra/template.yaml`, Policies de `QueryFunction`).
    - boto3>=1.34 (ya es dependencia del proyecto para Secrets Manager/DynamoDB).
    - NO requiere una API key nueva en Secrets Manager: usa las credenciales
      del rol de ejecución de Lambda vía la cadena de credenciales por
      defecto de boto3.

Este módulo NO se ejecuta en el entorno de verificación de este proyecto (sin
credenciales de AWS reales para invocar Bedrock) — se prueba a nivel de
ProviderRegistry con MockLLMAdapter (ver tests/test_provider_registry.py).
"""
import json
import os
import time
from typing import List

from .base import GenerationResult, HealthStatus, LLMProviderPort

try:
    import boto3
    from botocore.config import Config
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class BedrockEmbeddingAdapter(LLMProviderPort):
    """Respaldo 1 de `embed()` (ver módulo). `generate()` no está implementado
    a propósito — este adaptador no participa en la cadena de generación."""

    name = "bedrock"

    def __init__(self, model_id: str = None, region_name: str = None):
        if not _AVAILABLE:
            raise ImportError("boto3 no está instalado. Ejecuta: pip install boto3")
        self._model_id = model_id or os.getenv(
            "BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"
        )
        # AWS_REGION ya lo define Lambda automáticamente en tiempo de
        # ejecución; en local puede fijarse a mano si es necesario.
        self._region_name = region_name or os.getenv("AWS_REGION") or os.getenv("BEDROCK_REGION")
        self._request_timeout = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "6"))
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=self._region_name,
            config=Config(
                connect_timeout=5,
                read_timeout=self._request_timeout,
                retries={"max_attempts": 1},
            ),
        )

    def generate(self, prompt: str, **params) -> GenerationResult:
        raise NotImplementedError(
            "BedrockEmbeddingAdapter solo implementa embed() — no participa en la "
            "cadena de generate() (Gemini -> OpenAI/Groq -> Llama/OpenRouter)."
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            start = time.monotonic()
            body = json.dumps({"inputText": text})
            response = self._client.invoke_model(
                modelId=self._model_id,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
            payload = json.loads(response["body"].read())
            vectors.append(payload["embedding"])
            _ = (time.monotonic() - start) * 1000  # latencia por texto, no se agrega al resultado
        return vectors

    def health_check(self) -> HealthStatus:
        try:
            self.embed(["ping"])
            return HealthStatus(healthy=True)
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(healthy=False, detail=str(exc))
