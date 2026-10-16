"""Verifica que ResourceManager dispare la ingesta RAG y la precarga CAG al
publicar material (Iteración 6, FR5, integrando con la Iteración 5)."""
import unittest

from src.adapters.mock_adapter import MockLLMAdapter
from src.cag.cache_store import CAGStore
from src.courses.catalog import CourseCatalogService, CourseNotFoundError
from src.courses.resource_manager import ResourceManager
from src.retrieval.inmemory_store import InMemoryVectorStore


class TestResourceManager(unittest.TestCase):
    def setUp(self):
        self.catalog = CourseCatalogService()
        self.catalog.create_course("ISIS-1234", "Arquitectura de Software")
        self.vector_store = InMemoryVectorStore()
        self.cag_store = CAGStore()
        self.embedding_provider = MockLLMAdapter(name="gemini")
        self.manager = ResourceManager(
            catalog=self.catalog, vector_store=self.vector_store,
            cag_store=self.cag_store, embedding_provider=self.embedding_provider,
        )

    def test_publish_material_indexes_chunks(self):
        result = self.manager.publish_material(
            course_id="ISIS-1234", document_id="doc-1",
            text="contenido del syllabus " * 100,
        )
        self.assertGreater(result["chunks_indexed"], 0)
        self.assertFalse(result["cag_preloaded"])

    def test_publish_material_with_cag_context_preloads(self):
        result = self.manager.publish_material(
            course_id="ISIS-1234", document_id="doc-1", text="contenido",
            cag_context="Reglas del curso: 3 parciales.",
        )
        self.assertTrue(result["cag_preloaded"])
        self.assertEqual(self.cag_store.get("ISIS-1234"), "Reglas del curso: 3 parciales.")

    def test_publish_material_unknown_course_raises(self):
        with self.assertRaises(CourseNotFoundError):
            self.manager.publish_material(
                course_id="NO-EXISTE", document_id="doc-1", text="contenido"
            )


if __name__ == "__main__":
    unittest.main()
