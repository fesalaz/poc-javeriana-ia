"""
Demo ejecutable de la PoC con adaptadores simulados (sin credenciales ni red).

Parte 1 (núcleo RAG/CAG): ingesta RAG, precarga CAG, una consulta que usa CAG,
una que usa RAG, un cache-hit semántico, y un fallback cuando el proveedor
primario falla.

Parte 2 (ampliación — Iteraciones 4 y 6): gestión de cursos, publicación de
material (dispara ingesta RAG + precarga CAG), registro de artefactos docentes
(embed y webhook), invocación de un artefacto webhook, y el tablero de
seguimiento de avance por estudiante/curso.

Ejecutar:  python3 run_demo.py
"""
from src.adapters.mock_adapter import MockLLMAdapter
from src.cache.semantic_cache import SemanticResponseCache
from src.cag.cache_store import CAGStore
from src.config import build_application
from src.orchestrator.context_assembler import ContextAssembler
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.router import Router
from src.registry.circuit_breaker import CircuitBreaker
from src.registry.provider_registry import ProviderRegistry
from src.retrieval.inmemory_store import InMemoryVectorStore
from src.retrieval.ingestion import ingest_document


class DemoHttpClient:
    """Doble de un cliente HTTP (como `requests`) para simular la invocación de
    un artefacto docente tipo 'webhook' sin necesitar red."""

    def __init__(self, reply="¡Claro! Repasemos el tema.", should_fail=False):
        self.reply = reply
        self.should_fail = should_fail
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        if self.should_fail:
            raise ConnectionError("fallo simulado de red hacia el artefacto")

        class _Response:
            def __init__(self, reply):
                self.status_code = 200
                self._reply = reply

            def json(self):
                return {"reply": self._reply, "metadata": {"topic": "repaso"}}

        return _Response(self.reply)


def line(title):
    print("\n" + "=" * 8 + f" {title} " + "=" * 8)


def main():
    # 3 proveedores, todos de tier gratuito en la implementación real (ver
    # src/config.py): Gemini (primario) -> OpenAI gpt-oss-20b/Groq (respaldo 1)
    # -> Llama 3.3/OpenRouter (respaldo 2). Aquí se simulan con MockLLMAdapter.
    gemini = MockLLMAdapter(name="gemini")
    openai = MockLLMAdapter(name="openai")
    llama = MockLLMAdapter(name="llama")

    registry = ProviderRegistry()
    registry.register(gemini, priority=1, breaker=CircuitBreaker(failure_threshold=3))
    registry.register(openai, priority=2, breaker=CircuitBreaker(failure_threshold=3))
    registry.register(llama, priority=3, breaker=CircuitBreaker())

    vector_store = InMemoryVectorStore()
    cag_store = CAGStore()
    semantic_cache = SemanticResponseCache()
    orchestrator = Orchestrator(
        provider_registry=registry,
        vector_store=vector_store,
        cag_store=cag_store,
        semantic_cache=semantic_cache,
        context_assembler=ContextAssembler(),
        router=Router(cag_store),
        top_k=3,
        score_threshold=0.0,
    )

    line("1) Ingesta RAG para el curso ISIS-1234")
    n_chunks = ingest_document(
        text=(
            "El curso ISIS-1234 cubre arquitectura de software. "
            "El método ADD 3.0 del SEI permite diseñar a partir de atributos de calidad. "
            "RAG combina recuperación de información con generación de texto. "
            "CAG precarga contexto para reducir la latencia de las respuestas."
        ),
        course_id="ISIS-1234",
        document_id="syllabus-01",
        embedding_provider=registry,
        vector_store=vector_store,
    )
    print(f"Chunks indexados: {n_chunks}")

    line("2) Consulta usando RAG (curso sin CAG precargado)")
    r1 = orchestrator.handle_query("est-001", "ISIS-1234", "¿Qué es el método ADD 3.0?")
    print(r1)

    line("3) Misma pregunta otra vez -> debería ser cache-hit semántico")
    r2 = orchestrator.handle_query("est-002", "ISIS-1234", "¿Qué es el método ADD 3.0?")
    print(r2)
    assert r2["source"] == "semantic_cache", "se esperaba un hit del caché semántico"

    line("4) Precarga CAG para el curso ISIS-5678 y consulta")
    cag_store.preload("ISIS-5678", context="Reglas del curso: 3 parciales, sin celulares en examen.")
    r3 = orchestrator.handle_query("est-003", "ISIS-5678", "¿Cuántos parciales tiene el curso?")
    print(r3)
    assert r3["used_cag"] is True

    line("5) Falla del proveedor primario (gemini) -> fallback a openai (respaldo 1)")
    gemini.set_healthy(False)
    r4 = orchestrator.handle_query("est-004", "ISIS-1234", "¿Qué es RAG?")
    print(r4)
    assert r4["source"] == "llm:openai", "se esperaba fallback a openai"

    line("5b) También falla openai -> fallback a llama (respaldo 2, tercer proveedor)")
    openai.set_healthy(False)
    r4b = orchestrator.handle_query("est-004b", "ISIS-1234", "¿Qué es CAG?")
    print(r4b)
    assert r4b["source"] == "llm:llama", "se esperaba fallback en cadena hasta llama"
    openai.set_healthy(True)  # restaurar para el resto de la demo

    line("6) Repetir fallas para abrir el circuit breaker de gemini")
    for _ in range(3):
        try:
            orchestrator.handle_query("est-005", "ISIS-1234", f"pregunta distinta {_}")
        except Exception:
            pass
    print("Estado de proveedores:", registry.list_providers())

    line("Parte 2: Gestión de cursos y Registro de artefactos (Iteraciones 4 y 6)")

    http_client = DemoHttpClient(reply="¡Claro! El parcial 2 cubre pilas y colas.")
    app = build_application(use_real_providers=False, http_client=http_client)

    line("7) Crear curso e inscribir estudiante/profesor")
    app.catalog.create_course("ISIS-9999", "Estructuras de Datos")
    app.catalog.enroll_student("ISIS-9999", "est-100")
    app.catalog.assign_teacher("ISIS-9999", "prof-1")
    print("Curso creado:", app.catalog.get_course("ISIS-9999"))

    line("8) Profesor publica material -> dispara ingesta RAG + precarga CAG")
    publish_result = app.resource_manager.publish_material(
        course_id="ISIS-9999",
        document_id="guia-parciales",
        text="El curso de Estructuras de Datos cubre listas, pilas, colas y árboles. " * 10,
        cag_context="Reglas del curso: 2 parciales y 1 proyecto final.",
        cag_ttl_seconds=3600,
    )
    print(publish_result)

    line("9) Estudiante consulta -> se registra en el Activity Logger (sin guardar el texto)")
    q_result = app.orchestrator.handle_query("est-100", "ISIS-9999", "¿cuántos parciales tiene el curso?")
    print(q_result)

    line("10) Profesor registra un artefacto propio (embed y webhook) y el estudiante usa el webhook")
    app.artifact_registry.register_artifact({
        "artifact_id": "quiz-embebido",
        "course_id": "ISIS-9999",
        "name": "Quiz interactivo del curso",
        "artifact_type": "embed",
        "url": "https://quiz.javerianacali.edu.co/estructuras-datos",
        "allowed_domains": ["javerianacali.edu.co"],
    })
    app.artifact_registry.register_artifact({
        "artifact_id": "tutor-ia-docente",
        "course_id": "ISIS-9999",
        "name": "Tutor de repaso (creado por el profesor)",
        "artifact_type": "webhook",
        "url": "https://tutor-prof1.example.com/webhook",
    })
    artifact_result = app.artifact_invoker.invoke(
        "tutor-ia-docente", "est-100", "ISIS-9999", "¿qué entra en el parcial 2?"
    )
    print("Respuesta del artefacto docente:", artifact_result)
    embed_info = app.artifact_invoker.get_embed_info("quiz-embebido")
    print("Info para incrustar el quiz:", embed_info)

    line("11) Profesor consulta el tablero de seguimiento del curso")
    app.progress_aggregator.aggregate("ISIS-9999")
    print("Avance de est-100:", app.dashboard.get_progress("est-100", "ISIS-9999"))
    print("Resumen del curso (todos los estudiantes):", app.dashboard.get_course_overview("ISIS-9999"))

    line("12) Falla del artefacto webhook -> se marca no disponible sin afectar el resto")
    http_client.should_fail = True
    try:
        app.artifact_invoker.invoke("tutor-ia-docente", "est-100", "ISIS-9999", "otra pregunta")
    except Exception as exc:
        print("Fallo esperado:", exc)
    print("¿Artefacto disponible tras la falla?:", app.artifact_registry.is_available("tutor-ia-docente"))
    # El resto del sistema (Orquestador) sigue funcionando con normalidad:
    print(app.orchestrator.handle_query("est-100", "ISIS-9999", "¿el curso tiene proyecto final?"))

    line("Demo completada sin errores")


if __name__ == "__main__":
    main()
