"""
Implementación REAL de VectorStorePort sobre PostgreSQL + pgvector, vía LangChain
(restricción C1 y C3 del diseño). Requiere (en tu entorno):

    pip install langchain-postgres psycopg[binary] pgvector
    docker compose up -d db   # ver docker-compose.yml (imagen con extensión pgvector)

No se ejecuta en el entorno de verificación de este proyecto (no hay Postgres ni
estas dependencias disponibles ahí) — la lógica de recuperación se verifica con
InMemoryVectorStore, que implementa el mismo puerto (VectorStorePort).
"""
import os
from typing import Any, Dict, List, Optional

from .store_base import VectorStorePort

try:
    from langchain_postgres import PGVector
    from langchain_core.embeddings import Embeddings
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class _StaticEmbeddings:
    """Adaptador puente: LangChain espera un objeto Embeddings, pero en esta
    arquitectura los embeddings se calculan vía LLMProviderPort.embed() (agnóstico
    de proveedor). Esta clase solo delega."""

    def __init__(self, embedding_provider):
        self._provider = embedding_provider

    def embed_documents(self, texts):
        return self._provider.embed(texts)

    def embed_query(self, text):
        return self._provider.embed([text])[0]


class PGVectorStore(VectorStorePort):
    def __init__(self, embedding_provider, connection_string: str = None,
                 collection_name: str = "javeriana_ia_chunks"):
        if not _AVAILABLE:
            raise ImportError(
                "langchain-postgres / psycopg / pgvector no están instalados. "
                "Ejecuta: pip install langchain-postgres 'psycopg[binary]' pgvector"
            )
        # connect_timeout (parámetro libpq/psycopg): evita que una conexión a
        # una BD inalcanzable cuelgue la construcción de este store —
        # confirmado como causa real de un hang en el PoC desplegado (ver
        # nota de módulo en lambda_handler.py). Solo se agrega en el default
        # de este fallback local; si POSTGRES_URL viene de _build_postgres_url()
        # (el caso real en AWS) ya trae su propio connect_timeout.
        conn = connection_string or os.getenv(
            "POSTGRES_URL",
            "postgresql+psycopg://postgres:postgres@localhost:5432/javeriana_ia?connect_timeout=5",
        )
        self._store = PGVector(
            embeddings=_StaticEmbeddings(embedding_provider),
            collection_name=collection_name,
            connection=conn,
            use_jsonb=True,
        )

    def add(self, chunk_id, text, embedding, course_id, metadata: Optional[Dict[str, Any]] = None):
        meta = dict(metadata or {})
        meta["course_id"] = course_id
        self._store.add_texts(texts=[text], metadatas=[meta], ids=[chunk_id])

    def search(self, query_embedding, course_id, top_k=5, score_threshold=0.0):
        # NOTA: PGVector filtra por metadata; se usa el mismo campo course_id
        # que en la Iteración 5/7 (aislamiento de datos entre cursos).
        results = self._store.similarity_search_by_vector(
            embedding=query_embedding, k=top_k, filter={"course_id": course_id}
        )
        return [
            {"chunk_id": doc.id, "text": doc.page_content, "score": None, "metadata": doc.metadata}
            for doc in results
        ]
