"""Unit tests for QuizBotPro health check, readiness architecture, and deployment verification.

Validates:
1. Root '/' backward compatibility for cloud health checks.
2. Liveness endpoint '/live' responds 200 while process and HTTP server are alive.
3. Readiness endpoint '/ready' returns HTTP 503 while initializing and HTTP 200 when ready.
4. Health endpoint '/health' and '/status' compatibility reflecting application readiness.
5. Version endpoint '/version' reporting commit SHA, environment, and readiness status.
6. Safe fallback when RENDER_GIT_COMMIT is missing.
7. HTTP HEAD request handling across routes and readiness states.
8. 404 responses for unknown routes.
9. Zero secrets / sensitive credentials leakage in health/readiness responses.
10. Deployment verifier retry on HTTP 503, exact SHA matching, and environment matching/rejection.
"""

import asyncio
import importlib.util
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.main import (
    HealthCheckHandler,
    _wait_for_application_running,
    is_application_ready,
    reset_application_readiness,
    set_application_ready,
)

_script_path = Path(__file__).resolve().parent.parent / ".github" / "verify_deployment.py"
_spec = importlib.util.spec_from_file_location("verify_deployment_module", _script_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
verify_deployment = _module.verify_deployment


@pytest.fixture(autouse=True)
def _reset_readiness_state():
    """Ensure clean readiness state before and after each test."""
    reset_application_readiness()
    yield
    reset_application_readiness()


class _DummyHttpRequest:
    """Mock socket request object for lightweight, deterministic HTTP handler testing."""

    def __init__(self, request_bytes: bytes):
        self._rfile = io.BytesIO(request_bytes)
        self._wfile = io.BytesIO()

    def makefile(self, mode: str, *args, **kwargs):
        if "r" in mode:
            return self._rfile
        return self._wfile

    def sendall(self, data: bytes):
        self._wfile.write(data)


def _execute_request(method: str, path: str) -> tuple[int, dict[str, str], str]:
    """Execute an HTTP request through HealthCheckHandler and parse the response."""
    raw_req = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
    req = _DummyHttpRequest(raw_req)
    HealthCheckHandler(req, ("127.0.0.1", 12345), MagicMock())

    raw_response = req._wfile.getvalue().decode("utf-8")
    parts = raw_response.split("\r\n\r\n", 1)
    headers_part = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    header_lines = headers_part.split("\r\n")
    status_line = header_lines[0]
    status_code = int(status_line.split(" ")[1])

    headers = {}
    for line in header_lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v

    return status_code, headers, body


def test_root_path_backward_compatibility():
    """GET '/' must return HTTP 200 with exact legacy text response for Render."""
    status, headers, body = _execute_request("GET", "/")
    assert status == 200
    assert "text/plain" in headers.get("content-type", "")
    assert body == "OK - QuizBotPro is active and healthy"


def test_liveness_endpoint_always_200_even_when_unready():
    """GET '/live' must respond HTTP 200 'alive' even when application is not yet ready."""
    assert is_application_ready() is False
    status, headers, body = _execute_request("GET", "/live")

    assert status == 200
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "alive"
    assert "commit" in data
    assert "environment" in data


def test_readiness_endpoint_503_while_not_ready():
    """GET '/ready' must return HTTP 503 with not_ready status during initialization."""
    assert is_application_ready() is False
    status, headers, body = _execute_request("GET", "/ready")

    assert status == 503
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "not_ready"
    assert data["ready"] is False
    assert data["reason"] == "initializing"
    assert "commit" in data
    assert "environment" in data


def test_readiness_endpoint_200_when_ready():
    """GET '/ready' must return HTTP 200 with ready=True once startup succeeds."""
    set_application_ready(True)
    status, headers, body = _execute_request("GET", "/ready")

    assert status == 200
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "ready"
    assert data["ready"] is True
    assert "commit" in data
    assert "environment" in data


def test_readiness_false_before_and_after_database_init_failure():
    """Readiness must remain False and return 503 if database initialization fails."""
    assert is_application_ready() is False

    # Simulate database initialization failure updating readiness
    set_application_ready(False, reason="database_initialization_failed")
    status, _headers, body = _execute_request("GET", "/ready")

    assert status == 503
    data = json.loads(body)
    assert data["status"] == "not_ready"
    assert data["ready"] is False
    assert data["reason"] == "database_initialization_failed"


def test_readiness_reset_and_shutdown_behaviour():
    """Readiness must transition from True to False upon shutdown or reset."""
    set_application_ready(True)
    assert is_application_ready() is True

    # Transition to shutdown
    set_application_ready(False, reason="application_shutdown")
    assert is_application_ready() is False

    status, _, body = _execute_request("GET", "/ready")
    assert status == 503
    data = json.loads(body)
    assert data["status"] == "not_ready"
    assert data["reason"] == "application_shutdown"

    # Reset
    reset_application_readiness()
    assert is_application_ready() is False
    status, _, body = _execute_request("GET", "/ready")
    assert status == 503
    data = json.loads(body)
    assert data["reason"] == "initializing"


def test_health_endpoint_default_commit_when_ready(monkeypatch):
    """GET '/health' returns JSON with default 'local-dev' commit when ready."""
    set_application_ready(True)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    status, headers, body = _execute_request("GET", "/health")

    assert status == 200
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "healthy"
    assert data["ready"] is True
    assert data["commit"] == "local-dev"
    assert "environment" in data


def test_health_endpoint_with_render_git_commit_when_ready(monkeypatch):
    """GET '/health' returns the exact commit SHA injected by Render when ready."""
    set_application_ready(True)
    expected_sha = "9fb721b322161451cc9b41e86a1a315b88b69f6e"
    monkeypatch.setenv("RENDER_GIT_COMMIT", expected_sha)

    status, headers, body = _execute_request("GET", "/health")
    assert status == 200
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "healthy"
    assert data["commit"] == expected_sha


def test_health_endpoint_returns_503_when_not_ready():
    """GET '/health' returns HTTP 503 unhealthy when application is not ready."""
    assert is_application_ready() is False
    status, headers, body = _execute_request("GET", "/health")

    assert status == 503
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "unhealthy"
    assert data["ready"] is False
    assert data["reason"] == "initializing"


def test_version_endpoint_aliases_and_query_params():
    """Verify endpoint aliases, trailing slashes, and query strings are handled cleanly."""
    set_application_ready(True)
    for path in ["/version", "/status", "/health/", "/version?ref=latest"]:
        status, headers, body = _execute_request("GET", path)
        assert status == 200
        assert "application/json" in headers.get("content-type", "")

        data = json.loads(body)
        assert data["status"] in ("healthy", "ready")
        assert "commit" in data


def test_head_requests_across_endpoints():
    """HEAD requests must return expected HTTP status codes without payload body."""
    # Root and live are always 200
    status, _headers, body = _execute_request("HEAD", "/")
    assert status == 200
    assert body == ""

    status, _headers, body = _execute_request("HEAD", "/live")
    assert status == 200
    assert body == ""

    status, _headers, body = _execute_request("HEAD", "/version")
    assert status == 200
    assert body == ""

    # Ready and health return 503 when not ready
    status, _headers, body = _execute_request("HEAD", "/ready")
    assert status == 503
    assert body == ""

    status, _headers, body = _execute_request("HEAD", "/health")
    assert status == 503
    assert body == ""

    # Once ready, ready and health return 200
    set_application_ready(True)

    status, _headers, body = _execute_request("HEAD", "/ready")
    assert status == 200
    assert body == ""

    status, _headers, body = _execute_request("HEAD", "/health")
    assert status == 200
    assert body == ""


def test_unknown_routes_return_404():
    """Unrecognized paths must return HTTP 404."""
    status, _headers, body = _execute_request("GET", "/unknown")
    assert status == 404
    assert body == "Not Found"

    status, _headers, body = _execute_request("HEAD", "/not-a-valid-route")
    assert status == 404
    assert body == ""


def test_no_secrets_leaked_in_health_response(monkeypatch):
    """Verify that sensitive environment variables are NEVER exposed in health responses."""
    set_application_ready(True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "sensitive_super_secret_token_123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@secret-db.internal:5432/quiz")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSySecretKey999")

    for path in ["/live", "/ready", "/health", "/version"]:
        status, _headers, body = _execute_request("GET", path)
        assert status == 200

        assert "sensitive_super_secret_token_123" not in body
        assert "secret-db.internal" not in body
        assert "AIzaSySecretKey999" not in body

        data = json.loads(body)
        allowed_keys = {"status", "commit", "environment", "ready", "reason", "polling"}
        assert set(data.keys()).issubset(allowed_keys)
        if "polling" in data and data["polling"] is not None:
            allowed_polling_keys = {
                "healthy",
                "last_success_seconds_ago",
                "consecutive_errors",
                "consecutive_conflicts",
                "total_conflicts",
            }
            assert set(data["polling"].keys()).issubset(allowed_polling_keys)


@pytest.mark.asyncio
async def test_wait_for_application_running_lifecycle():
    """Verify _wait_for_application_running transitions readiness once app and updater are running."""
    from app.bot.polling_tracker import get_polling_tracker
    from app.readiness import get_application_readiness

    assert is_application_ready() is False

    mock_app = MagicMock()
    mock_app.running = False
    mock_app.updater = MagicMock()
    mock_app.updater.running = False

    task = asyncio.create_task(_wait_for_application_running(mock_app))

    # Give loop a cycle; should still be unready
    await asyncio.sleep(0.02)
    assert is_application_ready() is False

    # Simulate app and updater running
    mock_app.running = True
    mock_app.updater.running = True

    await task
    status = get_application_readiness()
    assert status["lifecycle_ready"] is True

    # With first polling success, application transitions to fully ready
    get_polling_tracker().record_success()
    assert is_application_ready() is True


def test_verify_deployment_success(monkeypatch):
    """Test verify_deployment script helper under successful condition."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(
        {
            "status": "ready",
            "ready": True,
            "commit": "abcdef1234567890abcdef1234567890abcdef12",
            "environment": "staging",
        }
    ).encode("utf-8")

    class MockContextManager:
        def __enter__(self):
            return mock_resp

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: MockContextManager())

    result = verify_deployment(
        target_sha="abcdef1234567890abcdef1234567890abcdef12",
        health_url="http://test.invalid/ready",
        timeout_seconds=5,
        poll_interval=1,
        expected_env="staging",
    )
    assert result is True


def test_verify_deployment_retries_on_503(monkeypatch):
    """Test verify_deployment retries during HTTP 503 startup and succeeds once ready."""
    call_count = 0

    def mock_urlopen(req, timeout):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            fp = io.BytesIO(json.dumps({"status": "not_ready", "reason": "initializing"}).encode())
            raise urllib.error.HTTPError(
                url="http://test.invalid/ready",
                code=503,
                msg="Service Unavailable",
                hdrs={},
                fp=fp,
            )

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(
            {
                "status": "ready",
                "ready": True,
                "commit": "abcdef1234567890",
                "environment": "production",
            }
        ).encode("utf-8")

        class MockContextManager:
            def __enter__(self):
                return mock_resp

            def __exit__(self, *args):
                pass

        return MockContextManager()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    result = verify_deployment(
        target_sha="abcdef1234567890",
        health_url="http://test.invalid/ready",
        timeout_seconds=5,
        poll_interval=0,
        expected_env="production",
    )
    assert result is True
    assert call_count == 3


def test_verify_deployment_rejects_incorrect_sha(monkeypatch):
    """Test verify_deployment fails when deployed commit does not match target SHA."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(
        {
            "status": "ready",
            "ready": True,
            "commit": "different_old_sha_1234567890",
            "environment": "staging",
        }
    ).encode("utf-8")

    class MockContextManager:
        def __enter__(self):
            return mock_resp

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: MockContextManager())

    result = verify_deployment(
        target_sha="target_sha_999999999",
        health_url="http://test.invalid/ready",
        timeout_seconds=1,
        poll_interval=0,
        expected_env="staging",
    )
    assert result is False


def test_verify_deployment_rejects_incorrect_environment(monkeypatch):
    """Test verify_deployment fails immediately on environment mismatch to prevent cross-talk."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(
        {
            "status": "ready",
            "ready": True,
            "commit": "abcdef1234567890abcdef1234567890abcdef12",
            "environment": "staging",
        }
    ).encode("utf-8")

    class MockContextManager:
        def __enter__(self):
            return mock_resp

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: MockContextManager())

    # Target is staging, but expected_env is production -> must abort and return False
    result = verify_deployment(
        target_sha="abcdef1234567890abcdef1234567890abcdef12",
        health_url="http://test.invalid/ready",
        timeout_seconds=10,
        poll_interval=1,
        expected_env="production",
    )
    assert result is False


def test_verify_deployment_timeout(monkeypatch):
    """Test verify_deployment script helper times out and returns False on persistent error."""

    def failing_urlopen(req, timeout):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", failing_urlopen)

    result = verify_deployment(
        target_sha="abcdef1234567890",
        health_url="http://test.invalid/ready",
        timeout_seconds=1,
        poll_interval=1,
    )
    assert result is False
