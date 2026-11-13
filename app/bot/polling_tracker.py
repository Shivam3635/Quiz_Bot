"""Telegram polling health and telemetry tracker for QuizBotPro.

Provides a thread-safe tracker to monitor Telegram getUpdates roundtrips,
detect HTTP 409 Conflict storms during rolling deployments, and verify
polling health independently of user message traffic.
"""

import threading
import time
from typing import Any


class PollingTracker:
    """Thread-safe telemetry tracker for Telegram getUpdates long-polling."""

    def __init__(
        self,
        max_consecutive_conflicts: int = 3,
        poll_timeout_seconds: float = 45.0,
    ) -> None:
        self._lock = threading.RLock()
        self.max_consecutive_conflicts: int = max_consecutive_conflicts
        self.poll_timeout_seconds: float = poll_timeout_seconds

        self._total_attempts: int = 0
        self._total_successes: int = 0
        self._last_success_time: float | None = None
        self._consecutive_errors: int = 0
        self._total_conflicts: int = 0
        self._consecutive_conflicts: int = 0
        self._last_error_category: str | None = None
        self._polling_started: bool = False
        self._has_polled_successfully: bool = False

    def record_attempt(self) -> None:
        """Record an outgoing getUpdates polling attempt."""
        with self._lock:
            self._total_attempts += 1
            self._polling_started = True

    def record_success(self) -> None:
        """Record a successful getUpdates roundtrip (even with an empty result list)."""
        with self._lock:
            self._total_successes += 1
            self._has_polled_successfully = True
            self._last_success_time = time.monotonic()
            self._consecutive_errors = 0
            self._consecutive_conflicts = 0
            self._last_error_category = None

    def record_conflict(self) -> None:
        """Record an HTTP 409 Conflict event indicating another active polling client."""
        with self._lock:
            self._total_conflicts += 1
            self._consecutive_conflicts += 1
            self._consecutive_errors += 1
            self._last_error_category = "conflict_409"

    def record_error(self, error: Exception | str) -> None:
        """Record a non-conflict polling failure (network error, timeout, 5xx, etc.)."""
        with self._lock:
            self._consecutive_errors += 1
            if isinstance(error, Exception):
                self._last_error_category = error.__class__.__name__
            else:
                self._last_error_category = str(error)

    def is_healthy(self) -> bool:
        """Determine whether Telegram polling is currently healthy."""
        with self._lock:
            # Polling cannot be considered healthy until at least one getUpdates has succeeded
            if not self._has_polled_successfully:
                return False

            # Persistent 409 conflicts indicate another instance is stealing updates
            if self._consecutive_conflicts >= self.max_consecutive_conflicts:
                return False

            # Polling timeout: no successful getUpdates response within the allowed window
            if self._last_success_time is not None:
                elapsed = time.monotonic() - self._last_success_time
                if elapsed > self.poll_timeout_seconds:
                    return False

            return True

    def get_unhealthy_reason(self) -> str | None:
        """Return a safe machine-readable reason when polling is unhealthy."""
        with self._lock:
            if not self._has_polled_successfully:
                return "polling_not_started" if not self._polling_started else "awaiting_first_poll"
            if self._consecutive_conflicts >= self.max_consecutive_conflicts:
                return "telegram_409_conflict"
            if self._last_success_time is not None:
                elapsed = time.monotonic() - self._last_success_time
                if elapsed > self.poll_timeout_seconds:
                    return "polling_timeout"
            return None

    def get_status(self) -> dict[str, Any]:
        """Return a thread-safe snapshot of polling telemetry for health endpoints."""
        with self._lock:
            elapsed_since_success = (
                round(time.monotonic() - self._last_success_time, 2)
                if self._last_success_time is not None
                else None
            )
            return {
                "healthy": self.is_healthy(),
                "total_attempts": self._total_attempts,
                "total_successes": self._total_successes,
                "last_success_seconds_ago": elapsed_since_success,
                "consecutive_errors": self._consecutive_errors,
                "consecutive_conflicts": self._consecutive_conflicts,
                "total_conflicts": self._total_conflicts,
                "last_error_category": self._last_error_category,
                "polling_started": self._polling_started,
                "has_polled_successfully": self._has_polled_successfully,
            }

    def reset(self) -> None:
        """Reset all polling metrics to initial unstarted state."""
        with self._lock:
            self._total_attempts = 0
            self._total_successes = 0
            self._last_success_time = None
            self._consecutive_errors = 0
            self._total_conflicts = 0
            self._consecutive_conflicts = 0
            self._last_error_category = None
            self._polling_started = False
            self._has_polled_successfully = False


# Module-level default singleton
_default_polling_tracker = PollingTracker()


def get_polling_tracker() -> PollingTracker:
    """Return the global default PollingTracker instance."""
    return _default_polling_tracker


def reset_polling_tracker() -> None:
    """Reset the global default PollingTracker instance."""
    _default_polling_tracker.reset()
