"""Unit tests for QuizBotPro health check and version verification endpoint.

Validates:
1. Root '/' backward compatibility for cloud health checks.
2. '/health', '/version', and '/status' JSON responses with RENDER_GIT_COMMIT.
3. Safe fallback when RENDER_GIT_COMMIT is missing.
4. HTTP HEAD request handling.
5. 404 responses for unknown routes.
6. Zero secrets / sensitive credentials leakage in responses.
"""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.main import HealthCheckHandler

_script_path = Path(__file__).resolve().parent.parent / ".github" / "verify_deployment.py"
_spec = importlib.util.spec_from_file_location("verify_deployment_module", _script_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
verify_deployment = _module.verify_deployment


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
    """GET '/' must return HTTP 200 with exact legacy text response."""
    status, headers, body = _execute_request("GET", "/")
    assert status == 200
    assert "text/plain" in headers.get("content-type", "")
    assert body == "OK - QuizBotPro is active and healthy"


def test_health_endpoint_default_commit(monkeypatch):
    """GET '/health' returns JSON with default 'local-dev' commit when RENDER_GIT_COMMIT unset."""
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    status, headers, body = _execute_request("GET", "/health")

    assert status == 200
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "healthy"
    assert data["commit"] == "local-dev"
    assert "environment" in data


def test_health_endpoint_with_render_git_commit(monkeypatch):
    """GET '/health' returns the exact commit SHA injected by Render."""
    expected_sha = "9fb721b322161451cc9b41e86a1a315b88b69f6e"
    monkeypatch.setenv("RENDER_GIT_COMMIT", expected_sha)

    status, headers, body = _execute_request("GET", "/health")
    assert status == 200
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "healthy"
    assert data["commit"] == expected_sha


@pytest.mark.parametrize("path", ["/version", "/status", "/health/", "/version?ref=latest"])
def test_health_endpoint_aliases_and_query_params(path):
    """Verify endpoint aliases, trailing slashes, and query strings are handled cleanly."""
    status, headers, body = _execute_request("GET", path)
    assert status == 200
    assert "application/json" in headers.get("content-type", "")

    data = json.loads(body)
    assert data["status"] == "healthy"
    assert "commit" in data


def test_head_requests():
    """HEAD requests must return HTTP 200 without payload body."""
    status, _headers, body = _execute_request("HEAD", "/")
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
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "sensitive_super_secret_token_123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@secret-db.internal:5432/quiz")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSySecretKey999")

    status, _headers, body = _execute_request("GET", "/health")
    assert status == 200

    assert "sensitive_super_secret_token_123" not in body
    assert "secret-db.internal" not in body
    assert "AIzaSySecretKey999" not in body

    # Explicit whitelist validation: Only authorized keys should exist in the response
    data = json.loads(body)
    allowed_keys = {"status", "commit", "environment"}
    assert set(data.keys()).issubset(allowed_keys)


def test_verify_deployment_success(monkeypatch):
    """Test verify_deployment script helper under successful condition."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "status": "healthy",
        "commit": "abcdef1234567890abcdef1234567890abcdef12",
        "environment": "staging",
    }).encode("utf-8")

    class MockContextManager:
        def __enter__(self):
            return mock_resp

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: MockContextManager())

    result = verify_deployment(
        target_sha="abcdef1234567890abcdef1234567890abcdef12",
        health_url="http://test.invalid/health",
        timeout_seconds=5,
        poll_interval=1,
    )
    assert result is True


def test_verify_deployment_timeout(monkeypatch):
    """Test verify_deployment script helper times out and returns False on persistent error."""
    def failing_urlopen(req, timeout):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", failing_urlopen)

    result = verify_deployment(
        target_sha="abcdef1234567890",
        health_url="http://test.invalid/health",
        timeout_seconds=1,
        poll_interval=1,
    )
    assert result is False

