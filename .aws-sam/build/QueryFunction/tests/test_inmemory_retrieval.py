"""Verifica el aislamiento por curso, topK y umbral de similitud del vector store
(Iteración 5/7: el filtro course_id es también un control de autorización)."""
import unittest

from src.retrieval.inmemory_store import InMemoryVectorStore
from src.retrieval.ingestion import chunk_document, ingest_document


class TestInMemoryRetrieval(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryVectorStore()
        self.store.add("c1", "chunk curso A", [1.0, 0.0, 0.0], course_id="A")
        self.store.add("c2", "chunk curso A relacionado", [0.9, 0.1, 0.0], course_id="A")
        self.store.add("c3", "chunk curso B", [0.0, 1.0, 0.0], course_id="B")

    def test_filters_by_course(self):
        results = self.store.search([1.0, 0.0, 0.0], course_id="A", top_k=10)
        self.assertTrue(all(r["chunk_id"] in ("c1", "c2") for r in results))
        self.assertNotIn("c3", [r["chunk_id"] for r in results])

    def test_respects_top_k(self):
        results = self.store.search([1.0, 0.0, 0.0], course_id="A", top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "c1")

    def test_respects_score_threshold(self):
        results = self.store.search([0.0, 1.0, 0.0], course_id="A", top_k=10, score_threshold=0.5)
        self.assertEqual(results, [])

    def test_chunking_fallback_without_langchain(self):
        text = "x" * 1000
        chunks = chunk_document(text, chunk_size=300, chunk_overlap=50)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 300 for c in chunks))

    def test_ingest_document_populates_store(self):
        class FakeEmbeddingProvider:
            def embed(self, texts):
                return [[float(len(t))] for t in texts]

        store = InMemoryVectorStore()
        n = ingest_document(
            text="contenido de prueba " * 50,
            course_id="A",
            document_id="doc1",
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=store,
            chunk_size=100,
            chunk_overlap=10,
        )
        self.assertGreater(n, 0)
        results = store.search([100.0], course_id="A", top_k=5)
        self.assertGreater(len(results), 0)


if __name__ == "__main__":
    unittest.main()
