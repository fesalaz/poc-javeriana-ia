"""
Resource Manager (Iteración 6, FR5): al publicar material de un curso, dispara
el pipeline de ingesta RAG (Iteración 5) y, opcionalmente, la precarga de CAG —
sin duplicar esa lógica, solo orquestando las llamadas.
"""
from typing import Optional

from ..retrieval.ingestion import ingest_document


class ResourceManager:
    def __init__(self, catalog, vector_store, cag_store, embedding_provider):
        self.catalog = catalog
        self.vector_store = vector_store
        self.cag_store = cag_store
        self.embedding_provider = embedding_provider

    def publish_material(self, course_id: str, document_id: str, text: str,
                          cag_context: Optional[str] = None,
                          cag_ttl_seconds: Optional[float] = None) -> dict:
        self.catalog.get_course(course_id)  # valida que el curso exista (lanza si no)

        n_chunks = ingest_document(
            text=text,
            course_id=course_id,
            document_id=document_id,
            embedding_provider=self.embedding_provider,
            vector_store=self.vector_store,
        )

        preloaded = False
        if cag_context is not None:
            self.cag_store.preload(course_id, cag_context, ttl_seconds=cag_ttl_seconds)
            preloaded = True

        return {"chunks_indexed": n_chunks, "cag_preloaded": preloaded}
