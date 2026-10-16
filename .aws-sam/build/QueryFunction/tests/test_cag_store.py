"""Verifica precarga/invalidación/TTL del CAG Store (Iteración 1/5, QAS-P2)."""
import unittest

from src.cag.cache_store import CAGStore


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestCAGStore(unittest.TestCase):
    def test_preload_and_get(self):
        store = CAGStore()
        store.preload("curso-A", "contexto del curso")
        self.assertTrue(store.has("curso-A"))
        self.assertEqual(store.get("curso-A"), "contexto del curso")

    def test_missing_course_returns_none(self):
        store = CAGStore()
        self.assertIsNone(store.get("curso-inexistente"))
        self.assertFalse(store.has("curso-inexistente"))

    def test_ttl_expiration(self):
        clock = FakeClock()
        store = CAGStore(clock=clock)
        store.preload("curso-A", "contexto", ttl_seconds=100)
        clock.advance(101)
        self.assertIsNone(store.get("curso-A"))

    def test_invalidate(self):
        store = CAGStore()
        store.preload("curso-A", "contexto")
        store.invalidate("curso-A")
        self.assertFalse(store.has("curso-A"))

    def test_republish_updates_context(self):
        store = CAGStore()
        store.preload("curso-A", "v1")
        store.preload("curso-A", "v2")
        self.assertEqual(store.get("curso-A"), "v2")


if __name__ == "__main__":
    unittest.main()
