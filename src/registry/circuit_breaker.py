"""
Máquina de estados del circuit breaker — parámetros definidos en la Iteración 3
del diseño ADD 3.0 (QAS-D1): CLOSED -> OPEN tras >=5 fallos consecutivos o tasa de
error >50% en ventana de 30s; OPEN -> HALF_OPEN tras 30s; HALF_OPEN -> CLOSED si la
petición de prueba tiene éxito (reintroducción), o -> OPEN si falla.

El parámetro `clock` permite inyectar un reloj falso en las pruebas (evita esperar
30s reales).
"""
import time
from enum import Enum


class CBState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, error_rate_threshold: float = 0.5,
                 window_seconds: float = 30, open_duration_seconds: float = 30,
                 min_requests_for_rate: int = 4, clock=time.monotonic):
        self.failure_threshold = failure_threshold
        self.error_rate_threshold = error_rate_threshold
        self.window_seconds = window_seconds
        self.open_duration_seconds = open_duration_seconds
        # La tasa de error solo se evalúa con una muestra mínima de peticiones en la
        # ventana; evita abrir el circuito de forma prematura con 1-2 eventos.
        self.min_requests_for_rate = min_requests_for_rate
        self._clock = clock
        self.state = CBState.CLOSED
        self._events = []  # list[(timestamp, ok: bool)]
        self._opened_at = None

    def _prune(self):
        now = self._clock()
        self._events = [(t, ok) for t, ok in self._events if now - t <= self.window_seconds]

    def allow_request(self) -> bool:
        if self.state == CBState.OPEN:
            if self._clock() - self._opened_at >= self.open_duration_seconds:
                self.state = CBState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self):
        self._events.append((self._clock(), True))
        self._prune()
        if self.state == CBState.HALF_OPEN:
            self.state = CBState.CLOSED
            self._events.clear()

    def record_failure(self):
        self._events.append((self._clock(), False))
        self._prune()
        if self.state == CBState.HALF_OPEN:
            self.state = CBState.OPEN
            self._opened_at = self._clock()
            return

        consecutive_failures = 0
        for _, ok in reversed(self._events):
            if ok:
                break
            consecutive_failures += 1

        total = len(self._events)
        failures = sum(1 for _, ok in self._events if not ok)
        error_rate = failures / total if total else 0.0
        rate_triggers = total >= self.min_requests_for_rate and error_rate > self.error_rate_threshold

        if consecutive_failures >= self.failure_threshold or rate_triggers:
            self.state = CBState.OPEN
            self._opened_at = self._clock()
