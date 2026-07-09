from __future__ import annotations
import time
from repopilot.llm.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_circuit_starts_closed():
    cb = CircuitBreaker(window=10, min_calls=3, error_rate=0.5, cooldown=0.1)
    assert cb.state == "closed"
    assert cb.allow_request() is True


def test_circuit_opens_after_threshold():
    cb = CircuitBreaker(window=10, min_calls=3, error_rate=0.5, cooldown=10)
    for _ in range(5):
        cb.record_failure()
    assert cb.allow_request() is False
    assert cb.state == "open"


def test_circuit_open_raises_nothing():
    cb = CircuitBreaker(window=10, min_calls=3, error_rate=0.5, cooldown=10)
    for _ in range(5):
        cb.record_failure()
    assert cb.allow_request() is False


def test_circuit_half_open_after_cooldown():
    cb = CircuitBreaker(window=10, min_calls=2, error_rate=0.5, cooldown=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.allow_request() is False
    time.sleep(0.1)
    # After cooldown, one probe is allowed (half-open)
    assert cb.allow_request() is True
    assert cb.state == "half-open"
    cb.record_success()  # probe succeeds -> close again
    assert cb.state == "closed"
    assert cb.allow_request() is True


def test_circuit_half_open_failure_reopens():
    cb = CircuitBreaker(window=10, min_calls=2, error_rate=0.5, cooldown=0.05)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.1)
    cb.allow_request()  # enter half-open
    cb.record_failure()
    assert cb.state == "open"


def test_circuit_reset():
    cb = CircuitBreaker(window=10, min_calls=2, error_rate=0.5, cooldown=10)
    cb.record_failure(); cb.record_failure()
    assert cb.state == "open"
    cb.reset()
    assert cb.state == "closed"
    assert cb.allow_request() is True


def test_successes_keep_circuit_closed():
    cb = CircuitBreaker(window=10, min_calls=3, error_rate=0.5)
    for _ in range(10):
        cb.record_success()
    assert cb.state == "closed"
    assert cb.allow_request() is True
