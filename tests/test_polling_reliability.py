"""Unit tests for QuizBotPro Telegram polling reliability and 409 Conflict telemetry.

Validates Phase 3 requirements:
1. Successful getUpdates with empty result ({"ok": true, "result": []}) marks polling healthy.
2. Successful getUpdates with non-empty updates marks polling healthy.
3. HTTP 409 Conflict detection in MonitoredHTTPXRequest.do_request.
4. Exception-based 409 Conflict handling in MonitoredHTTPXRequest.do_request.
5. 3 consecutive 409 conflicts make polling unhealthy (is_healthy() == False).
6. Successful poll resets consecutive conflicts to 0.
7. Transient network error recovery (e.g. TimedOut then 200 OK).
8. Polling timeout (> 45s without successful poll) makes readiness False (polling_timeout).
9. Quiet bot with 0 messages remains healthy through empty result updates.
10. Polling tracker is NOT healthy before first successful poll (awaiting_first_poll / polling_not_started).
11. Application cannot become ready before PTB lifecycle initialization (lifecycle_ready == False).
12. Polling failure makes /ready return HTTP 503 with safe reason.
13. /live remains HTTP 200 during polling failure.
14. Polling recovery restores readiness when lifecycle remains valid.
15. Shutdown lifecycle hooks (post_stop / post_shutdown) reset readiness and tracker.
16. Zero secrets / bot tokens leaked in polling telemetry or HTTP responses.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import Conflict, TimedOut

from app.bot.monitored_request import MonitoredHTTPXRequest
from app.bot.polling_tracker import (
    PollingTracker,
    get_polling_tracker,
    reset_polling_tracker,
)
from app.main import (
    is_application_ready,
    post_shutdown,
    post_stop,
    reset_application_readiness,
    set_application_lifecycle_ready,
    set_application_ready,
)
from app.readiness import (
    ReadinessManager,
    get_application_readiness,
)
from tests.test_health_endpoint import _execute_request


@pytest.fixture(autouse=True)
def _reset_state():
    """Ensure clean readiness and polling tracker state before and after each test."""
    reset_application_readiness()
    reset_polling_tracker()
    yield
    reset_application_readiness()
    reset_polling_tracker()


@pytest.mark.asyncio
async def test_successful_poll_empty_result_marks_healthy():
    """Empty update list from getUpdates represents a valid poll roundtrip and marks tracker healthy."""
    tracker = PollingTracker()
    request = MonitoredHTTPXRequest(polling_tracker=tracker)

    with patch(
        "telegram.request.HTTPXRequest.do_request",
        new_callable=AsyncMock,
        return_value=(200, b'{"ok":true,"result":[]}'),
    ):
        code, _ = await request.do_request(
            url="https://api.telegram.org/bot12345/getUpdates",
            method="POST",
        )

    assert code == 200
    assert tracker.is_healthy() is True
    status = tracker.get_status()
    assert status["total_attempts"] == 1
    assert status["total_successes"] == 1
    assert status["consecutive_errors"] == 0
    assert status["consecutive_conflicts"] == 0
    assert status["has_polled_successfully"] is True


@pytest.mark.asyncio
async def test_successful_poll_non_empty_updates_marks_healthy():
    """Non-empty updates result correctly increments total successes and marks tracker healthy."""
    tracker = PollingTracker()
    request = MonitoredHTTPXRequest(polling_tracker=tracker)

    payload_data = b'{"ok":true,"result":[{"update_id":1001,"message":{"message_id":1}}]}'
    with patch(
        "telegram.request.HTTPXRequest.do_request",
        new_callable=AsyncMock,
        return_value=(200, payload_data),
    ):
        code, _ = await request.do_request(
            url="https://api.telegram.org/bot12345/getUpdates",
            method="POST",
        )

    assert code == 200
    assert tracker.is_healthy() is True
    assert tracker.get_status()["total_successes"] == 1


@pytest.mark.asyncio
async def test_http_409_conflict_status_code_detection():
    """HTTP 409 status code returned by Telegram is detected and logged as a conflict."""
    tracker = PollingTracker()
    request = MonitoredHTTPXRequest(polling_tracker=tracker)

    payload_409 = b'{"ok":false,"error_code":409,"description":"Conflict: terminated by other getUpdates request"}'
    with patch(
        "telegram.request.HTTPXRequest.do_request",
        new_callable=AsyncMock,
        return_value=(409, payload_409),
    ):
        code, _ = await request.do_request(
            url="https://api.telegram.org/bot12345/getUpdates",
            method="POST",
        )

    assert code == 409
    status = tracker.get_status()
    assert status["total_conflicts"] == 1
    assert status["consecutive_conflicts"] == 1
    assert status["consecutive_errors"] == 1
    assert status["last_error_category"] == "conflict_409"


@pytest.mark.asyncio
async def test_exception_based_conflict_handling():
    """PTB Conflict exception raised during do_request is intercepted, tracked, and re-raised."""
    tracker = PollingTracker()
    request = MonitoredHTTPXRequest(polling_tracker=tracker)

    with (
        patch(
            "telegram.request.HTTPXRequest.do_request",
            new_callable=AsyncMock,
            side_effect=Conflict("Terminated by other getUpdates request"),
        ),
        pytest.raises(Conflict) as exc_info,
    ):
        await request.do_request(
            url="https://api.telegram.org/bot12345/getUpdates",
            method="POST",
        )

    assert "Terminated by other getUpdates request" in str(exc_info.value)
    status = tracker.get_status()
    assert status["total_conflicts"] == 1
    assert status["consecutive_conflicts"] == 1
    assert status["last_error_category"] == "conflict_409"


def test_consecutive_409_conflicts_makes_unhealthy():
    """Threshold of 3 consecutive 409 conflicts transitions tracker to unhealthy."""
    tracker = PollingTracker(max_consecutive_conflicts=3)
    tracker.record_attempt()
    tracker.record_success()
    assert tracker.is_healthy() is True

    # 1st conflict
    tracker.record_conflict()
    assert tracker.is_healthy() is True

    # 2nd conflict
    tracker.record_conflict()
    assert tracker.is_healthy() is True

    # 3rd conflict reaches threshold
    tracker.record_conflict()
    assert tracker.is_healthy() is False
    assert tracker.get_unhealthy_reason() == "telegram_409_conflict"


def test_successful_poll_resets_consecutive_conflicts():
    """A subsequent successful poll resets consecutive conflicts back to 0."""
    tracker = PollingTracker(max_consecutive_conflicts=3)
    tracker.record_attempt()
    tracker.record_success()

    tracker.record_conflict()
    tracker.record_conflict()
    assert tracker.get_status()["consecutive_conflicts"] == 2

    tracker.record_success()
    status = tracker.get_status()
    assert status["consecutive_conflicts"] == 0
    assert status["consecutive_errors"] == 0
    assert status["total_conflicts"] == 2
    assert tracker.is_healthy() is True


@pytest.mark.asyncio
async def test_transient_network_error_recovery():
    """Transient network timeout is recorded and cleared upon subsequent successful poll."""
    tracker = PollingTracker()
    request = MonitoredHTTPXRequest(polling_tracker=tracker)

    # First successful poll
    tracker.record_attempt()
    tracker.record_success()
    assert tracker.is_healthy() is True

    # Transient network error
    with (
        patch(
            "telegram.request.HTTPXRequest.do_request",
            new_callable=AsyncMock,
            side_effect=TimedOut("Socket read timeout"),
        ),
        pytest.raises(TimedOut),
    ):
        await request.do_request(
            url="https://api.telegram.org/bot12345/getUpdates",
            method="POST",
        )

    assert tracker.get_status()["consecutive_errors"] == 1
    assert tracker.get_status()["last_error_category"] == "TimedOut"

    # Recovery poll
    with patch(
        "telegram.request.HTTPXRequest.do_request",
        new_callable=AsyncMock,
        return_value=(200, b'{"ok":true,"result":[]}'),
    ):
        await request.do_request(
            url="https://api.telegram.org/bot12345/getUpdates",
            method="POST",
        )

    assert tracker.get_status()["consecutive_errors"] == 0
    assert tracker.get_status()["last_error_category"] is None
    assert tracker.is_healthy() is True


def test_polling_timeout_makes_readiness_false(monkeypatch):
    """Elapsed time exceeding poll_timeout_seconds marks polling unhealthy with polling_timeout."""
    fake_time = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_time)

    tracker = PollingTracker(poll_timeout_seconds=45.0)
    manager = ReadinessManager(polling_tracker=tracker)

    tracker.record_attempt()
    tracker.record_success()
    manager.set_lifecycle_ready(True)
    assert manager.is_ready() is True

    # Advance time by 46 seconds (> 45s threshold)
    fake_time += 46.0
    assert tracker.is_healthy() is False
    assert tracker.get_unhealthy_reason() == "polling_timeout"
    assert manager.is_ready() is False
    assert manager.get_status()["reason"] == "polling_timeout"


def test_quiet_bot_remains_healthy():
    """Bot with zero incoming user messages remains healthy across repeated empty results."""
    tracker = PollingTracker()
    for _ in range(10):
        tracker.record_attempt()
        tracker.record_success()

    status = tracker.get_status()
    assert status["healthy"] is True
    assert status["total_attempts"] == 10
    assert status["total_successes"] == 10
    assert status["consecutive_errors"] == 0
    assert tracker.is_healthy() is True


def test_tracker_unhealthy_before_first_poll():
    """PollingTracker is not healthy until at least one getUpdates roundtrip has succeeded."""
    tracker = PollingTracker()
    assert tracker.is_healthy() is False
    assert tracker.get_unhealthy_reason() == "polling_not_started"

    tracker.record_attempt()
    assert tracker.is_healthy() is False
    assert tracker.get_unhealthy_reason() == "awaiting_first_poll"


def test_application_cannot_be_ready_before_lifecycle_init():
    """Successful poll alone cannot make application ready before PTB lifecycle initializes."""
    tracker = PollingTracker()
    manager = ReadinessManager(polling_tracker=tracker)

    tracker.record_attempt()
    tracker.record_success()
    assert tracker.is_healthy() is True

    # Lifecycle readiness is still False
    assert manager.is_ready() is False
    status = manager.get_status()
    assert status["ready"] is False
    assert status["lifecycle_ready"] is False
    assert status["reason"] == "initializing"


def test_polling_failure_makes_ready_endpoint_return_503():
    """When polling encounters persistent conflicts, /ready returns HTTP 503 with safe reason."""
    tracker = get_polling_tracker()
    tracker.record_attempt()
    tracker.record_success()
    set_application_lifecycle_ready(True)
    assert is_application_ready() is True

    # Simulate 3 consecutive 409 conflicts
    for _ in range(3):
        tracker.record_conflict()

    assert is_application_ready() is False
    status, _, body = _execute_request("GET", "/ready")
    assert status == 503

    data = json.loads(body)
    assert data["status"] == "not_ready"
    assert data["ready"] is False
    assert data["reason"] == "telegram_409_conflict"
    assert "polling" in data
    assert data["polling"]["consecutive_conflicts"] == 3
    assert data["polling"]["healthy"] is False


def test_live_endpoint_remains_200_during_polling_failure():
    """Liveness probe /live remains HTTP 200 alive even during Telegram polling conflicts."""
    tracker = get_polling_tracker()
    tracker.record_attempt()
    tracker.record_success()
    set_application_lifecycle_ready(True)

    # Induce polling conflict failure
    for _ in range(3):
        tracker.record_conflict()

    status, _, body = _execute_request("GET", "/live")
    assert status == 200

    data = json.loads(body)
    assert data["status"] == "alive"


def test_polling_recovery_restores_readiness():
    """Recovering from polling conflict restores /ready HTTP 200 response."""
    tracker = get_polling_tracker()
    tracker.record_attempt()
    tracker.record_success()
    set_application_lifecycle_ready(True)

    # Polling conflict failure
    for _ in range(3):
        tracker.record_conflict()
    assert is_application_ready() is False

    # Polling resolves
    tracker.record_success()
    assert is_application_ready() is True

    status, _, body = _execute_request("GET", "/ready")
    assert status == 200
    data = json.loads(body)
    assert data["status"] == "ready"
    assert data["ready"] is True
    assert data["polling"]["healthy"] is True


@pytest.mark.asyncio
async def test_shutdown_lifecycle_hooks_reset_readiness_and_tracker():
    """post_stop and post_shutdown hooks reset both lifecycle readiness and polling tracker."""
    tracker = get_polling_tracker()
    set_application_ready(True)
    tracker.record_attempt()
    tracker.record_success()
    assert is_application_ready() is True

    mock_app = MagicMock()

    # Test post_stop
    await post_stop(mock_app)
    assert is_application_ready() is False
    status = get_application_readiness()
    assert status["ready"] is False
    assert status["reason"] == "application_stopped"

    # Re-enable and test post_shutdown
    set_application_ready(True)
    assert is_application_ready() is True

    await post_shutdown(mock_app)
    assert is_application_ready() is False
    status = get_application_readiness()
    assert status["ready"] is False
    assert status["reason"] == "application_shutdown"


@pytest.mark.asyncio
async def test_no_secrets_leaked_in_polling_telemetry(monkeypatch):
    """Ensure bot tokens and credentials in request URLs are never exposed in telemetry or responses."""
    tracker = get_polling_tracker()
    request = MonitoredHTTPXRequest(polling_tracker=tracker)

    sensitive_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567"
    sensitive_url = f"https://api.telegram.org/bot{sensitive_token}/getUpdates"

    with patch(
        "telegram.request.HTTPXRequest.do_request",
        new_callable=AsyncMock,
        return_value=(200, b'{"ok":true,"result":[]}'),
    ):
        await request.do_request(url=sensitive_url, method="POST")

    set_application_lifecycle_ready(True)
    assert is_application_ready() is True

    for endpoint in ("/ready", "/health", "/status"):
        status, _, body = _execute_request("GET", endpoint)
        assert status == 200
        assert sensitive_token not in body
        assert "bot123456789" not in body

        data = json.loads(body)
        assert "polling" in data
        polling = data["polling"]
        allowed_keys = {
            "healthy",
            "last_success_seconds_ago",
            "consecutive_errors",
            "consecutive_conflicts",
            "total_conflicts",
        }
        assert set(polling.keys()).issubset(allowed_keys)
