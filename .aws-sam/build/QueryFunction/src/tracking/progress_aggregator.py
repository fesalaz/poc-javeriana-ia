"""
Progress Aggregator (Iteración 6, FR6 — CQRS ligero): consolida el Event Log en
una vista de avance materializada por curso/estudiante, para que las consultas
del profesor no compitan con el tráfico de escritura de eventos.
"""
from collections import defaultdict
from typing import Dict


def _empty_stats():
    return {"query_count": 0, "artifact_invocations": 0, "last_activity": None}


class ProgressAggregator:
    def __init__(self, activity_logger):
        self.activity_logger = activity_logger
        self._view: Dict[str, Dict[str, dict]] = {}

    def aggregate(self, course_id: str) -> None:
        events = self.activity_logger.events_for_course(course_id)
        per_student = defaultdict(_empty_stats)
        for event in events:
            stats = per_student[event.student_id]
            if event.event_type == "query":
                stats["query_count"] += 1
            elif event.event_type == "artifact_invoked":
                stats["artifact_invocations"] += 1
            if stats["last_activity"] is None or event.timestamp > stats["last_activity"]:
                stats["last_activity"] = event.timestamp
        self._view[course_id] = dict(per_student)

    def get_student_progress(self, course_id: str, student_id: str) -> dict:
        return self._view.get(course_id, {}).get(student_id, _empty_stats())

    def get_course_overview(self, course_id: str) -> dict:
        return dict(self._view.get(course_id, {}))
