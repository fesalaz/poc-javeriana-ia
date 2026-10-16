"""Verifica la máquina de estados del circuit breaker (Iteración 3, QAS-D1)."""
import unittest

from src.registry.circuit_breaker import CBState, CircuitBreaker


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_consecutive_failures(self):
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=3, clock=clock)
        self.assertEqual(cb.state, CBState.CLOSED)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CBState.CLOSED)
        cb.record_failure()
        self.assertEqual(cb.state, CBState.OPEN)

    def test_blocks_requests_while_open(self):
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, open_duration_seconds=30, clock=clock)
        cb.record_failure()
        self.assertEqual(cb.state, CBState.OPEN)
        self.assertFalse(cb.allow_request())

    def test_half_open_after_wait_and_recovers_on_success(self):
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, open_duration_seconds=30, clock=clock)
        cb.record_failure()
        self.assertFalse(cb.allow_request())
        clock.advance(30)
        self.assertTrue(cb.allow_request())
        self.assertEqual(cb.state, CBState.HALF_OPEN)
        cb.record_success()
        self.assertEqual(cb.state, CBState.CLOSED)

    def test_half_open_reopens_on_failure(self):
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, open_duration_seconds=30, clock=clock)
        cb.record_failure()
        clock.advance(30)
        self.assertTrue(cb.allow_request())
        cb.record_failure()
        self.assertEqual(cb.state, CBState.OPEN)

    def test_opens_on_error_rate_even_without_consecutive_run(self):
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=100, error_rate_threshold=0.5,
                             min_requests_for_rate=4, clock=clock)
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CBState.CLOSED)  # aún no hay muestra mínima (4)
        cb.record_failure()
        self.assertEqual(cb.state, CBState.OPEN)  # 4 eventos, 75% de error > 50%

    def test_rate_check_ignored_below_minimum_sample(self):
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=100, error_rate_threshold=0.5,
                             min_requests_for_rate=4, clock=clock)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CBState.CLOSED)


if __name__ == "__main__":
    unittest.main()
