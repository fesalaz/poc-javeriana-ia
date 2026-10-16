"""
Ensamblaje ("wiring") del sistema: construye el Provider Registry con los
adaptadores reales (Gemini, OpenAI gpt-oss-20b/Groq, Llama 3.3/OpenRouter) o
simulados según el entorno, y arma el Orquestador con sus dependencias. Ver
README para variables de entorno.

`build_application()` ensambla, además del núcleo RAG/CAG, los módulos de
Gestión de Cursos/Seguimiento (Iteración 6) y Registro de Artefactos Docentes
(Iteración 4) sobre las mismas piezas — un solo objeto `Application` con todo
conectado (incluye el Activity Logger inyectado en el Orquestador).

NOTA (agosto 2026 — checkpoints dentro de build_provider_registry, ver
GUIA_DESPLIEGUE_Y_PRUEBAS.md sección 0.2): los checkpoints de
lambda_handler.py revelaron que `build_provider_registry(use_real_providers=True)`
por sí sola tarda ~18-20s (de los 28s de Timeout del Lambda) — más que
cualquier llamada de red esperada. Hipótesis principal: los imports de
`langchain_google_genai`/`langchain_openai` (y sus dependencias transitivas —
grpc, protobuf, openai, httpx) son pesados, y con `MemorySize: 512` (en
infra/template.yaml) el Lambda solo recibe ~0.29 vCPU (la CPU escala lineal
con la memoria hasta 1792 MB = 1 vCPU completo), lo que hace ese import
mucho más lento que en una máquina de desarrollo normal. Se agregan
checkpoints por cada adaptador para confirmar si el costo está en los
imports o en la construcción de un adaptador específico.
"""
import os
import time
from dataclasses import dataclass

from .adapters.mock_adapter import MockLLMAdapter
from .artifacts.registry import ArtifactRegistry
from .artifacts.invoker import ArtifactInvoker
from .cache.semantic_cache import SemanticResponseCache
from .cag.cache_store import CAGStore
from .courses.catalog import CourseCatalogService
from .courses.resource_manager import ResourceManager
from .orchestrator.context_assembler import ContextAssembler
from .orchestrator.orchestrator import Orchestrator
from .orchestrator.router import Router
from .registry.circuit_breaker import CircuitBreaker
from .registry.provider_registry import ProviderRegistry
from .retrieval.inmemory_store import InMemoryVectorStore
from .tracking.activity_logger import ActivityLogger
from .tracking.progress_aggregator import ProgressAggregator
from .tracking.progress_dashboard import ProgressDashboardAPI


def build_provider_registry(use_real_providers: bool = False) -> ProviderRegistry:
    """Registra los cinco proveedores del PoC (actualizado septiembre 2026,
    ver hallazgo R-7 / T-4 en `EVALUACION_ATAM_LITE.md`):

    Cadena de `generate()` (sin cambios frente a la versión anterior):
    1) Gemini (Google AI Studio) — primario.
    2) OpenAI `gpt-oss-20b` servido por Groq — respaldo 1.
    3) Llama 3.3 servido por OpenRouter — respaldo 2.

    Cadena de `embed()` (reparada: antes solo tenía a Gemini, sin ningún
    respaldo real — OpenAI/Groq y Llama/OpenRouter nunca implementaron esta
    operación, y bajo la prueba de carga de 100 usuarios concurrentes el
    circuit breaker de Gemini se abrió sin que hubiera a dónde conmutar):
    1) Gemini — primario, también resuelve embeddings.
    4) AWS Bedrock, Titan Text Embeddings V2 — respaldo 1 (mismo cloud, sin
       credencial nueva, ver `bedrock_embedding_adapter.py`).
    5) Voyage AI, voyage-4-lite — respaldo 2 (proveedor independiente de AWS,
       ver `voyage_adapter.py`).

    Los proveedores 2 y 3 no implementan `embed()` (lanzan NotImplementedError
    y se saltan sin contar contra su circuit breaker); los proveedores 4 y 5
    no implementan `generate()` por el mismo mecanismo — así ambas cadenas
    conviven en un solo registro sin necesidad de dos ProviderRegistry
    separados. El orden (prioridad) es solo configuración: agregar, quitar o
    reordenar un proveedor no requiere tocar el Orquestador (QAS-I1)."""
    registry = ProviderRegistry()
    if use_real_providers:
        t0 = time.monotonic()

        def _cp(label):
            print(f"[provider-registry-init] {label} (+{time.monotonic() - t0:.2f}s)")

        # Requiere dependencias + credenciales reales (ver README). Se importan aquí
        # (no al tope del módulo) para que este archivo funcione sin ellas instaladas.
        _cp("importando adaptador Gemini (langchain-google-genai)...")
        from .adapters.gemini_adapter import GeminiAdapter
        _cp("import de Gemini completado; construyendo instancia...")
        registry.register(GeminiAdapter(), priority=1, breaker=CircuitBreaker())
        _cp("GeminiAdapter registrado")

        _cp("importando adaptador OpenAI/Groq (langchain-openai)...")
        from .adapters.openai_adapter import OpenAIAdapter
        _cp("import de OpenAI completado; construyendo instancia...")
        registry.register(OpenAIAdapter(), priority=2, breaker=CircuitBreaker())
        _cp("OpenAIAdapter registrado")

        _cp("importando adaptador Llama/OpenRouter (langchain-openai)...")
        from .adapters.llama_adapter import LlamaAdapter
        _cp("import de Llama completado; construyendo instancia...")
        registry.register(LlamaAdapter(), priority=3, breaker=CircuitBreaker())
        _cp("LlamaAdapter registrado")

        _cp("importando adaptador AWS Bedrock (boto3, embed() únicamente)...")
        from .adapters.bedrock_embedding_adapter import BedrockEmbeddingAdapter
        _cp("import de Bedrock completado; construyendo instancia...")
        registry.register(BedrockEmbeddingAdapter(), priority=4, breaker=CircuitBreaker())
        _cp("BedrockEmbeddingAdapter registrado (respaldo 1 de embed())")

        _cp("importando adaptador Voyage AI (requests, embed() únicamente)...")
        from .adapters.voyage_adapter import VoyageAdapter
        _cp("import de Voyage completado; construyendo instancia...")
        registry.register(VoyageAdapter(), priority=5, breaker=CircuitBreaker())
        _cp("VoyageAdapter registrado (respaldo 2 de embed()) — build_provider_registry() completo")
    else:
        # Adaptadores simulados: útiles para desarrollo/demo/pruebas sin credenciales.
        registry.register(MockLLMAdapter(name="gemini"), priority=1, breaker=CircuitBreaker())
        registry.register(MockLLMAdapter(name="openai"), priority=2, breaker=CircuitBreaker())
        registry.register(MockLLMAdapter(name="llama"), priority=3, breaker=CircuitBreaker())
    return registry


def build_course_catalog(use_real: bool = False):
    """FR5 — catálogo de cursos: `PostgresCourseCatalogService` (real, persiste
    en la misma base de datos de RAG) cuando `use_real=True`, o
    `CourseCatalogService` en memoria (pruebas/demo) en caso contrario. Mismo
    patrón que `build_provider_registry()`/`vector_store`/`cag_store`: la
    versión real requiere dependencias/infra reales y no se ejecuta en el
    entorno de verificación de este proyecto."""
    if use_real:
        from .courses.postgres_catalog import PostgresCourseCatalogService
        return PostgresCourseCatalogService()
    return CourseCatalogService()


def build_orchestrator(use_real_providers: bool = None, vector_store=None,
                        activity_logger=None, cag_store=None, registry=None) -> Orchestrator:
    """`registry` y `cag_store` se pueden inyectar (usado por lambda_handler.py
    para conectar PGVectorStore/DynamoCAGStore reales); si no se pasan, se
    construyen con las implementaciones por defecto (mocks/en memoria)."""
    if use_real_providers is None:
        use_real_providers = os.getenv("USE_REAL_LLM_PROVIDERS", "false").lower() == "true"

    registry = registry or build_provider_registry(use_real_providers=use_real_providers)
    vector_store = vector_store or InMemoryVectorStore()
    cag_store = cag_store or CAGStore()
    semantic_cache = SemanticResponseCache()
    assembler = ContextAssembler()
    router = Router(cag_store)

    return Orchestrator(
        provider_registry=registry,
        vector_store=vector_store,
        cag_store=cag_store,
        semantic_cache=semantic_cache,
        context_assembler=assembler,
        router=router,
        activity_logger=activity_logger,
    )


@dataclass
class Application:
    """Ensamblaje completo de la PoC ampliada: núcleo RAG/CAG + Gestión de
    Cursos/Seguimiento (Iteración 6) + Registro de Artefactos (Iteración 4)."""

    orchestrator: Orchestrator
    catalog: CourseCatalogService
    resource_manager: ResourceManager
    artifact_registry: ArtifactRegistry
    artifact_invoker: ArtifactInvoker
    activity_logger: ActivityLogger
    progress_aggregator: ProgressAggregator
    dashboard: ProgressDashboardAPI


def build_application(use_real_providers: bool = None, http_client=None) -> Application:
    vector_store = InMemoryVectorStore()
    activity_logger = ActivityLogger()

    orchestrator = build_orchestrator(
        use_real_providers=use_real_providers,
        vector_store=vector_store,
        activity_logger=activity_logger,
    )

    catalog = CourseCatalogService()
    resource_manager = ResourceManager(
        catalog=catalog,
        vector_store=vector_store,
        cag_store=orchestrator.cag_store,
        embedding_provider=orchestrator.embedding_provider,
    )

    artifact_registry = ArtifactRegistry()
    artifact_invoker = ArtifactInvoker(
        registry=artifact_registry, http_client=http_client, activity_logger=activity_logger
    )

    progress_aggregator = ProgressAggregator(activity_logger)
    dashboard = ProgressDashboardAPI(progress_aggregator)

    return Application(
        orchestrator=orchestrator,
        catalog=catalog,
        resource_manager=resource_manager,
        artifact_registry=artifact_registry,
        artifact_invoker=artifact_invoker,
        activity_logger=activity_logger,
        progress_aggregator=progress_aggregator,
        dashboard=dashboard,
    )
