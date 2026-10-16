"""
Pipeline de ingesta RAG (Iteración 5): Document Loader -> Text Splitter -> Embeddings
-> Vector Store. Usa el splitter de LangChain si está disponible (tu entorno); si no
(como en el sandbox de verificación de este proyecto), usa un splitter simple de
respaldo por caracteres con solapamiento, para poder probar la lógica de ingesta
igual sin la dependencia instalada.
"""
from typing import List

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    _HAS_LANGCHAIN_SPLITTER = True
except ImportError:
    _HAS_LANGCHAIN_SPLITTER = False


def chunk_document(text: str, chunk_size: int = 800, chunk_overlap: int = 100) -> List[str]:
    if _HAS_LANGCHAIN_SPLITTER:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        return splitter.split_text(text)

    # Respaldo sin LangChain (solo para verificación local sin dependencias).
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap debe ser menor que chunk_size")
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        if end == n:
            break
        start = end - chunk_overlap
    return chunks


def ingest_document(text: str, course_id: str, document_id: str, embedding_provider,
                     vector_store, chunk_size: int = 800, chunk_overlap: int = 100) -> int:
    """Divide un documento en chunks, calcula embeddings vía LLMProviderPort.embed()
    (agnóstico de proveedor, Iteración 4) y los almacena en el vector store,
    particionados por course_id (Iteración 5/7)."""
    chunks = chunk_document(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not chunks:
        return 0
    embeddings = embedding_provider.embed(chunks)
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        vector_store.add(
            chunk_id=f"{document_id}-{i}",
            text=chunk,
            embedding=embedding,
            course_id=course_id,
            metadata={"document_id": document_id, "chunk_index": i},
        )
    return len(chunks)
