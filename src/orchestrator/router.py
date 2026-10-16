"""Router: decide si usar CAG (contexto de curso ya precargado, más rápido) o caer
a RAG (recuperación bajo demanda). Ver Iteraciones 1 y 2 del diseño."""


class Router:
    def __init__(self, cag_store):
        self.cag_store = cag_store

    def decide(self, course_id: str) -> dict:
        use_cag = self.cag_store.has(course_id)
        return {"use_cag": use_cag, "use_rag": not use_cag}
