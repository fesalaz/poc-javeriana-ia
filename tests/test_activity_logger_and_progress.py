"""Verifica ActivityLogger + ProgressAggregator + ProgressDashboardAPI
(Iteración 6, FR6 — CQRS ligero)."""
import unittest

from src.tracking.activity_logger import ActivityLogger
from src.tracking.progress_aggregator import ProgressAggregator
from src.tracking.progress_dashboard import ProgressDashboardAPI


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestActivityLoggerAndProgress(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.logger = ActivityLogger(clock=self.clock)
        self.aggregator = ProgressAggregator(self.logger)
        self.dashboard = ProgressDashboardAPI(self.aggregator)

    def test_no_events_returns_zeroed_progress(self):
        self.aggregator.aggregate("ISIS-1234")
        progress = self.dashboard.get_progress("est-1", "ISIS-1234")
        self.assertEqual(progress["query_count"], 0)
        self.assertIsNone(progress["last_activity"])

    def test_aggregates_query_and_artifact_events_per_student(self):
        self.logger.log_event("est-1", "ISIS-1234", "query", {"source": "llm:gemini"})
        self.clock.advance(1)
        self.logger.log_event("est-1", "ISIS-1234", "query", {"source": "semantic_cache"})
        self.clock.advance(1)
        self.logger.log_event("est-1", "ISIS-1234", "artifact_invoked", {"artifact_id": "art-1"})
        self.logger.log_event("est-2", "ISIS-1234", "query", {"source": "llm:llama"})

        self.aggregator.aggregate("ISIS-1234")

        p1 = self.dashboard.get_progress("est-1", "ISIS-1234")
        self.assertEqual(p1["query_count"], 2)
        self.assertEqual(p1["artifact_invocations"], 1)
        self.assertEqual(p1["last_activity"], 2.0)

        overview = self.dashboard.get_course_overview("ISIS-1234")
        self.assertEqual(set(overview.keys()), {"est-1", "est-2"})

    def test_events_scoped_by_course(self):
        self.logger.log_event("est-1", "ISIS-1234", "query", {})
        self.logger.log_event("est-1", "OTRO-999", "query", {})
        self.aggregator.aggregate("ISIS-1234")
        overview = self.dashboard.get_course_overview("ISIS-1234")
        self.assertEqual(overview["est-1"]["query_count"], 1)

    def test_activity_log_does_not_store_message_text(self):
        # Minimización de datos (Iteración 6): solo metadatos, nunca el mensaje.
        event = self.logger.log_event("est-1", "ISIS-1234", "query", {"source": "llm:gemini"})
        self.assertNotIn("message", event.metadata)
        self.assertNotIn("text", event.metadata)


if __name__ == "__main__":
    unittest.main()
