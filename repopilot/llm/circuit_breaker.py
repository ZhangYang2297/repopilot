from __future__ import annotations
import threading
import time
from collections import deque
from dataclasses import dataclass, field


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit breaker is open."""


@dataclass
class CircuitBreaker:
    """Sliding-window circuit breaker.

    States:
        closed  : requests flow through normally; failures recorded.
        open    : requests rejected immediately (raises CircuitOpenError).
        half-open: one probe request is allowed after cooldown; success closes,
                  failure re-opens.
    """

    window: int = 20
    min_calls: int = 5
    error_rate: float = 0.5
    cooldown: float = 60.0

    _state: str = field(default="closed", init=False)
    _opened_at: float = field(default=0.0, init=False)

    def __post_init__(self):
        self._calls: deque[int] = deque(maxlen=self.window)
        self._lock = threading.Lock()

    def allow_request(self) -> bool:
        """Return True if a request is allowed; False if circuit is open."""
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.time() - self._opened_at >= self.cooldown:
                    self._state = "half-open"
                    return True
                return False
            # half-open: allow exactly one probe
            return True

    def record_success(self):
        with self._lock:
            if self._state == "half-open":
                self._state = "closed"
                self._calls.clear()
            self._calls.append(1)

    def record_failure(self):
        with self._lock:
            self._calls.append(0)
            if self._state == "half-open":
                self._state = "open"
                self._opened_at = time.time()
                return
            if len(self._calls) >= self.min_calls:
                failures = sum(1 for c in self._calls if c == 0)
                if failures / len(self._calls) >= self.error_rate:
                    self._state = "open"
                    self._opened_at = time.time()

    def reset(self):
        """Force-close the breaker (for tests / recovery)."""
        with self._lock:
            self._calls.clear()
            self._state = "closed"

    @property
    def state(self) -> str:
        return self._state
