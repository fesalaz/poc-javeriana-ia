"""
Context Assembler (Iteración 2): combina el contexto CAG y/o los chunks RAG con la
pregunta del estudiante, respetando un presupuesto de contexto (aproximado por
caracteres en este PoC; en producción usar el tokenizer real del proveedor).
"""
from typing import List, Optional


class ContextAssembler:
    def __init__(self, budget_chars: int = 4000):
        self.budget_chars = budget_chars

    def assemble(self, query: str, cag_context: Optional[str] = None,
                 rag_chunks: Optional[List[dict]] = None) -> str:
        rag_chunks = rag_chunks or []
        parts = []
        if cag_context:
            parts.append(cag_context)
        for chunk in rag_chunks:
            parts.append(chunk["text"] if isinstance(chunk, dict) else str(chunk))
        context_text = "\n---\n".join(parts)
        if len(context_text) > self.budget_chars:
            context_text = context_text[: self.budget_chars] + "…"
        return (
            "Eres un asistente académico de la Universidad Javeriana Cali. "
            "Usa el siguiente contexto del curso para responder con precisión y, "
            "si el contexto no es suficiente, dilo explícitamente.\n\n"
            f"CONTEXTO:\n{context_text or '(sin contexto disponible)'}\n\n"
            f"PREGUNTA DEL ESTUDIANTE:\n{query}\n\nRESPUESTA:"
        )
