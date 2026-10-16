"""Puerto (interfaz) del vector store — permite intercambiar la implementación
in-memory (pruebas locales) por PGVectorStore (PostgreSQL+pgvector, producción)
sin tocar el Orquestador. Ver Iteración 5 del diseño ADD 3.0."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class VectorStorePort(ABC):
    @abstractmethod
    def add(self, chunk_id: str, text: str, embedding: List[float], course_id: str,
             metadata: Optional[Dict[str, Any]] = None) -> None:
        ...

    @abstractmethod
    def search(self, query_embedding: List[float], course_id: str, top_k: int = 5,
               score_threshold: float = 0.0) -> List[Dict[str, Any]]:
        ...
