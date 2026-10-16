"""
Caché KV precargada para CAG (Iteración 1 y 5 del diseño). En este PoC es en memoria;
en AWS se reemplaza por DynamoDB (con TTL nativo) o ElastiCache — ver infra/template.yaml.
Llave: course_id -> {context, version, expires_at}.
"""
import time
from typing import Optional


class CAGStore:
    def __init__(self, clock=time.monotonic):
        self._data = {}
        self._clock = clock

    def preload(self, course_id: str, context: str, ttl_seconds: Optional[float] = None,
                version: Optional[int] = None) -> None:
        expires_at = self._clock() + ttl_seconds if ttl_seconds else None
        self._data[course_id] = {
            "context": context,
            "version": version if version is not None else int(self._clock()),
            "expires_at": expires_at,
        }

    def get(self, course_id: str) -> Optional[str]:
        entry = self._data.get(course_id)
        if entry is None:
            return None
        if entry["expires_at"] is not None and self._clock() > entry["expires_at"]:
            del self._data[course_id]
            return None
        return entry["context"]

    def has(self, course_id: str) -> bool:
        return self.get(course_id) is not None

    def invalidate(self, course_id: str) -> None:
        self._data.pop(course_id, None)
