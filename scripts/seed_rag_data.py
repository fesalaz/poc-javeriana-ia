"""
Indexa un texto real en PostgreSQL+pgvector para poder probar el camino RAG
end-to-end contra el despliegue real en AWS (no contra el entorno local de
docker-compose, aunque también funciona ahí si POSTGRES_URL apunta a
localhost). No se ejecuta en el entorno de verificación de este proyecto
(requiere red + credenciales reales) — está pensado para correrlo en tu
máquina, DESPUÉS de desplegar y habilitar pgvector (ver
GUIA_DESPLIEGUE_Y_PRUEBAS.md, secciones 6.1 y 6.4).

ACTUALIZACIÓN (FR5): ya existe una ruta HTTP real de ingesta —
`POST /courses/{courseId}/material` (ver GUIA_DESPLIEGUE_Y_PRUEBAS.md,
sección 6.5), que llama a `ResourceManager.publish_material(...)` sobre
`PostgresCourseCatalogService`. Esa ruta es hoy la forma RECOMENDADA de
publicar material real contra el despliegue en AWS. Este script queda como
alternativa de más bajo nivel, útil para:
  - indexar contenido para un courseId que todavía no existe en el catálogo
    de cursos (esta vía no valida contra el catálogo, la API HTTP sí);
  - depurar el pipeline de ingesta (chunking + embeddings) de forma aislada,
    sin pasar por ResourceManager ni por la capa HTTP.

Motivo histórico por el que se creó: al momento de escribirlo, el Lambda
desplegado (`lambda_handler.py`) ensamblaba el Orquestador con
`build_orchestrator()` (src/config.py), que no incluía `ResourceManager` ni
`CourseCatalogService` — esos módulos solo existían en `build_application()`,
usado por run_demo.py y las pruebas. No había ninguna forma de llamar
`ResourceManager.publish_material(...)` contra el despliegue en AWS, así que
este script escribe directamente en el mismo vector store (PGVectorStore)
que consulta el Orquestador, usando el mismo pipeline de ingesta real
(chunk_document + embeddings vía Provider Registry). El hueco de la ruta
HTTP ya se cerró con FR5; este script se mantiene por los dos usos de bajo
nivel listados arriba.

Uso:
    export POSTGRES_URL="postgresql+psycopg://<DBUsername>:<DBPassword>@<PostgresEndpoint>:5432/javeriana_ia?connect_timeout=5"
    export GOOGLE_API_KEY=...   (Gemini es el único de los 3 proveedores con embed() real)
    python3 -m scripts.seed_rag_data ISIS-1234 "El curso de Estructuras de Datos cubre listas, pilas, colas y árboles. Tiene 3 parciales y un proyecto final."

Después de correrlo, prueba con el mismo courseId:
    python3 -m scripts.smoke_test_deployed_api <ApiUrl> ISIS-1234
y confirma en la respuesta que `rag_chunks` > 0 y que el texto generado
refleja el material indexado (no una respuesta genérica).
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Aviso: python-dotenv no está instalado; se usarán solo variables de entorno del sistema.")

from src.config import build_provider_registry
from src.retrieval.ingestion import ingest_document


def main():
    if len(sys.argv) < 3:
        sys.exit(
            "Uso: python3 -m scripts.seed_rag_data <courseId> <texto del material...>\n"
            "Ejemplo: python3 -m scripts.seed_rag_data ISIS-1234 \"El curso cubre listas, pilas y colas.\""
        )
    course_id = sys.argv[1]
    text = " ".join(sys.argv[2:])
    document_id = os.getenv("SEED_DOCUMENT_ID", "seed-1")

    if not os.getenv("POSTGRES_URL"):
        sys.exit(
            "Falta POSTGRES_URL. Debe apuntar al RDS real ya desplegado (no localhost), "
            "con la extensión pgvector habilitada (ver GUIA sección 6.1). Ejemplo:\n"
            '  export POSTGRES_URL="postgresql+psycopg://<user>:<pass>@<PostgresEndpoint>:5432/javeriana_ia?connect_timeout=5"'
        )
    if not os.getenv("GOOGLE_API_KEY"):
        sys.exit(
            "Falta GOOGLE_API_KEY. Gemini es el único de los 3 proveedores que resuelve "
            "embeddings de forma confiable (ver GUIA sección 0.1) — se necesita para calcular "
            "los embeddings de este texto antes de guardarlo."
        )

    print(f"Conectando a PostgreSQL ({os.getenv('POSTGRES_URL').split('@')[-1]})...")
    registry = build_provider_registry(use_real_providers=True)

    from src.retrieval.pgvector_store import PGVectorStore
    vector_store = PGVectorStore(embedding_provider=registry)

    n_chunks = ingest_document(
        text=text,
        course_id=course_id,
        document_id=document_id,
        embedding_provider=registry,
        vector_store=vector_store,
    )

    print(f"Indexados {n_chunks} chunk(s) para el curso '{course_id}' (document_id='{document_id}').")
    print(f"Ahora prueba: python3 -m scripts.smoke_test_deployed_api <ApiUrl> {course_id}")
    print("Deberías ver 'rag_chunks' > 0 en la respuesta.")


if __name__ == "__main__":
    main()
