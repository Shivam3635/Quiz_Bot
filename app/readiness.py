"""Application readiness state management for QuizBotPro.

Provides a thread-safe singleton mechanism to track whether QuizBotPro
has fully completed initialization (including database identity verification,
table creation, and Telegram polling/event-loop startup) and has healthy
Telegram polling telemetry (detecting 409 Conflict storms and network failures).
"""

import threading
from typing import Any

from app.bot.polling_tracker import PollingTracker, get_polling_tracker


class ReadinessManager:
    """Thread-safe application readiness state container combining lifecycle and polling health."""

    def __init__(self, polling_tracker: PollingTracker | None = None) -> None:
        self._lock = threading.RLock()
        self._lifecycle_ready: bool = False
        self._reason: str | None = "initializing"
        self._polling_tracker: PollingTracker = polling_tracker or get_polling_tracker()

    def set_ready(self, ready: bool, reason: str | None = None) -> None:
        """Update readiness state.

        When ready=True, marks both lifecycle readiness and records tracker success
        (used by tests and top-level lifecycle handlers).
        When ready=False, resets readiness and updates failure reason.
        """
        with self._lock:
            self._lifecycle_ready = ready
            if ready:
                self._reason = None
                if self._polling_tracker is not None:
                    self._polling_tracker.record_success()
            else:
                self._reason = reason or "not_ready"

    def set_lifecycle_ready(self, ready: bool, reason: str | None = None) -> None:
        """Update only the application/updater lifecycle readiness state."""
        with self._lock:
            self._lifecycle_ready = ready
            if ready:
                self._reason = None
            else:
                self._reason = reason or "not_ready"

    def is_ready(self) -> bool:
        """Check whether the application is fully ready (lifecycle ready AND polling healthy)."""
        with self._lock:
            if not self._lifecycle_ready:
                return False
            if self._polling_tracker is not None:
                return self._polling_tracker.is_healthy()
            return True

    def get_status(self) -> dict[str, Any]:
        """Return a snapshot of current readiness state and polling telemetry."""
        with self._lock:
            ready = self.is_ready()
            if not self._lifecycle_ready:
                reason = self._reason or "not_ready"
            elif self._polling_tracker is not None and not self._polling_tracker.is_healthy():
                reason = self._polling_tracker.get_unhealthy_reason() or "polling_unhealthy"
            else:
                reason = None

            status_dict: dict[str, Any] = {
                "ready": ready,
                "status": "ready" if ready else "not_ready",
                "reason": reason,
                "lifecycle_ready": self._lifecycle_ready,
            }

            if self._polling_tracker is not None:
                polling_info = self._polling_tracker.get_status()
                status_dict["polling"] = {
                    "healthy": polling_info["healthy"],
                    "last_success_seconds_ago": polling_info["last_success_seconds_ago"],
                    "consecutive_errors": polling_info["consecutive_errors"],
                    "consecutive_conflicts": polling_info["consecutive_conflicts"],
                    "total_conflicts": polling_info["total_conflicts"],
                }

            return status_dict

    def reset(self) -> None:
        """Reset the readiness state and bound polling tracker back to initial state."""
        with self._lock:
            self._lifecycle_ready = False
            self._reason = "initializing"
            if self._polling_tracker is not None:
                self._polling_tracker.reset()


# Process-wide singleton
_readiness_manager = ReadinessManager()


def set_application_ready(ready: bool, reason: str | None = None) -> None:
    """Set the application readiness state."""
    _readiness_manager.set_ready(ready, reason=reason)


def set_application_lifecycle_ready(ready: bool, reason: str | None = None) -> None:
    """Set the application lifecycle readiness state."""
    _readiness_manager.set_lifecycle_ready(ready, reason=reason)


def is_application_ready() -> bool:
    """Return whether the application is currently ready."""
    return _readiness_manager.is_ready()


def get_application_readiness() -> dict[str, Any]:
    """Get the full readiness dictionary status including polling telemetry."""
    return _readiness_manager.get_status()


def reset_application_readiness() -> None:
    """Reset the application readiness state and polling tracker to initial state."""
    _readiness_manager.reset()
