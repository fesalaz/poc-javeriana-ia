"""
Activity Logger + Event Log (Iteración 6, FR6): registro append-only de
eventos de actividad. Guarda solo metadatos (tipo de evento, timestamp,
curso/estudiante, latencia, etc.) — deliberadamente **sin el texto completo**
de las conversaciones, por minimización de datos (ver 1.6 y la Iteración 6).
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ActivityEvent:
    student_id: str
    course_id: str
    event_type: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class ActivityLogger:
    def __init__(self, clock=time.time):
        self._events: List[ActivityEvent] = []
        self._clock = clock

    def log_event(self, student_id: str, course_id: str, event_type: str,
                   metadata: Dict[str, Any] = None) -> ActivityEvent:
        event = ActivityEvent(
            student_id=student_id, course_id=course_id, event_type=event_type,
            metadata=metadata or {}, timestamp=self._clock(),
        )
        self._events.append(event)
        return event

    def events_for_course(self, course_id: str) -> List[ActivityEvent]:
        return [e for e in self._events if e.course_id == course_id]

    def events_for_student(self, course_id: str, student_id: str) -> List[ActivityEvent]:
        return [
            e for e in self._events
            if e.course_id == course_id and e.student_id == student_id
        ]
