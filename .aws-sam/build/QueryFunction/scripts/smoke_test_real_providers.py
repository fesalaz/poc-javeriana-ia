"""
Prueba de humo LOCAL con credenciales reales de los 3 proveedores (Gemini,
OpenAI gpt-oss-20b/Groq, Llama 3.3/OpenRouter) y, si POSTGRES_URL está
configurado, con PostgreSQL/pgvector real. No se ejecuta en el entorno de
verificación de este proyecto (requiere red + credenciales) — está pensado
para correrlo en tu máquina, DESPUÉS de completar el .env y ANTES de
desplegar en AWS (ver guía de despliegue, sección 3).

Ejecutar:  python3 -m scripts.smoke_test_real_providers
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Aviso: python-dotenv no está instalado; se usarán solo variables de entorno del sistema.")

from src.config import build_provider_registry


def main():
    print("== Prueba de humo: proveedores LLM reales ==\n")

    if not os.getenv("GOOGLE_API_KEY"):
        sys.exit("Falta GOOGLE_API_KEY (ver .env.example). Consigue una en aistudio.google.com.")
    if not os.getenv("OPENAI_OSS_API_KEY"):
        sys.exit("Falta OPENAI_OSS_API_KEY (ver .env.example). Consigue una en console.groq.com.")
    if not os.getenv("LLAMA_API_KEY"):
        sys.exit("Falta LLAMA_API_KEY (ver .env.example). Consigue una en openrouter.ai.")

    registry = build_provider_registry(use_real_providers=True)

    for name, _state in registry.list_providers():
        adapter = registry.get_adapter(name)
        print(f"--- Probando proveedor: {name} ---")
        health = adapter.health_check()
        print(f"health_check: {health}")
        if not health.healthy:
            print(f"  (se omite generate() para {name}: no está saludable)\n")
            continue
        try:
            result = adapter.generate("Responde en una frase: ¿qué es RAG?")
            print(f"generate() OK -> {result.text[:200]}")
        except Exception as exc:  # noqa: BLE001
            print(f"generate() FALLÓ: {exc}")
        print()

    print("== Prueba de humo: fallback automático vía ProviderRegistry ==")
    result = registry.generate("¿Qué es CAG en el contexto de LLMs?")
    print(f"Proveedor que respondió: {result.provider}")
    print(f"Respuesta: {result.text[:200]}")

    if os.getenv("POSTGRES_URL"):
        print("\n== Prueba de humo: PostgreSQL + pgvector ==")
        from src.retrieval.pgvector_store import PGVectorStore
        store = PGVectorStore(embedding_provider=registry)
        store.add("smoke-1", "El método ADD 3.0 estructura el diseño por atributos de calidad.",
                   registry.embed(["texto de prueba"])[0], course_id="SMOKE-TEST")
        results = store.search(registry.embed(["¿qué es ADD 3.0?"])[0], course_id="SMOKE-TEST", top_k=1)
        print(f"Chunks recuperados: {len(results)}")
    else:
        print("\n(Se omite la prueba de PostgreSQL: no se definió POSTGRES_URL)")

    print("\nListo. Si todo lo anterior respondió sin errores, ya puedes desplegar en AWS.")


if __name__ == "__main__":
    main()
