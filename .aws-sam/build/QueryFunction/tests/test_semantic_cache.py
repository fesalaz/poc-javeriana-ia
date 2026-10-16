"""Verifica el caché semántico de respuestas (Iteración 2, QAS-P1/P3)."""
import unittest

from src.cache.semantic_cache import SemanticResponseCache


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestSemanticCache(unittest.TestCase):
    def test_hit_on_identical_embedding(self):
        cache = SemanticResponseCache(similarity_threshold=0.95)
        emb = [1.0, 0.0, 0.0]
        cache.put("curso-A", emb, "respuesta cacheada")
        self.assertEqual(cache.get("curso-A", emb), "respuesta cacheada")

    def test_miss_on_dissimilar_embedding(self):
        cache = SemanticResponseCache(similarity_threshold=0.95)
        cache.put("curso-A", [1.0, 0.0, 0.0], "respuesta cacheada")
        self.assertIsNone(cache.get("curso-A", [0.0, 1.0, 0.0]))

    def test_scoped_by_course(self):
        cache = SemanticResponseCache(similarity_threshold=0.95)
        emb = [1.0, 0.0, 0.0]
        cache.put("curso-A", emb, "respuesta A")
        self.assertIsNone(cache.get("curso-B", emb))

    def test_ttl_expiration(self):
        clock = FakeClock()
        cache = SemanticResponseCache(similarity_threshold=0.95, ttl_seconds=60, clock=clock)
        emb = [1.0, 0.0, 0.0]
        cache.put("curso-A", emb, "respuesta")
        clock.advance(61)
        self.assertIsNone(cache.get("curso-A", emb))

    def test_invalidate_course(self):
        cache = SemanticResponseCache(similarity_threshold=0.95)
        emb = [1.0, 0.0, 0.0]
        cache.put("curso-A", emb, "respuesta")
        cache.invalidate_course("curso-A")
        self.assertIsNone(cache.get("curso-A", emb))


if __name__ == "__main__":
    unittest.main()
