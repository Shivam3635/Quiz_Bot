"""Unit and configuration tests for QuizBotPro Production Release Pipeline (M5).

Validates:
1. Production release workflow YAML schema, triggers, environment gates, and exact-SHA parameters.
2. Production deployment concurrency and secret isolation.
3. Database CLI utility operations (--verify-only, --status, --init, and fail-closed identity checks).
4. Operational documentation and rollback runbook completeness.
"""

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.database.database import DatabaseIdentityMismatchError, cli_main


def _get_workflow_content() -> str:
    workflow_path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deploy-production.yml"
    assert workflow_path.is_file(), "deploy-production.yml must exist in .github/workflows/"
    return workflow_path.read_text(encoding="utf-8")


def test_production_workflow_yaml_validity():
    """Verify deploy-production.yml exists, is non-empty, and has expected top-level structure."""
    content = _get_workflow_content()
    assert len(content) > 200
    assert "name: Production Release & Exact-SHA Deployment" in content
    assert "jobs:" in content
    assert "production-ci:" in content
    assert "deploy-production:" in content


def test_production_workflow_triggers_and_branch_isolation():
    """Ensure deploy-production.yml is restricted to main branch and does not trigger on develop."""
    content = _get_workflow_content()

    # Must contain push to main and workflow_dispatch
    assert "push:" in content
    assert "branches:" in content
    assert re.search(r"branches:\s*-\s*main", content), "Must trigger on push to main"

    # Must NOT list develop in push branches
    push_section = re.search(r"push:.*?(?=permissions:|\n\w+:)", content, re.DOTALL)
    assert push_section, "Must find push trigger section"
    assert "develop" not in push_section.group(0), "deploy-production.yml must never trigger on develop"

    # Least privilege permissions
    assert re.search(r"permissions:\s*contents:\s*read", content), "Must declare contents: read"


def test_production_deployment_job_security_and_gates():
    """Ensure production deployment job requires CI, environment approval, and exact-SHA parameter."""
    content = _get_workflow_content()

    # deploy-production must require production-ci
    assert re.search(r"needs:\s*production-ci", content), "deploy-production must depend on production-ci"

    # Must target protected 'production' environment
    assert re.search(r"environment:\s*\n\s*name:\s*production", content), "Must target environment: production"

    # Concurrency group with cancel-in-progress: false
    assert re.search(r"group:\s*production-deployment", content), "Must use production-deployment concurrency group"
    assert re.search(r"cancel-in-progress:\s*false", content), "Must enforce cancel-in-progress: false"

    # Verify steps contain exact-SHA parameter and deployment verification
    assert "Trigger Render Production Deployment" in content
    assert "Verify Production Health" in content
    assert "RENDER_PRODUCTION_DEPLOY_HOOK" in content
    assert "github.event.inputs.target_commit_sha || github.sha" in content
    assert "TARGET_SHA" in content
    assert ".github/verify_deployment.py" in content


def test_database_cli_verify_only_success(monkeypatch):
    """Verify database cli_main --verify-only executes verification and exits 0 on success."""
    monkeypatch.setattr(sys, "argv", ["database.py", "--verify-only"])
    with patch("app.database.database.verify_database_identity") as mock_verify:
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == 0
        mock_verify.assert_called_once()


def test_database_cli_status_success(monkeypatch):
    """Verify database cli_main --status inspects tables and exits 0 on success."""
    monkeypatch.setattr(sys, "argv", ["database.py", "--status"])
    with patch("app.database.database.verify_database_identity"):
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == 0


def test_database_cli_init_success(monkeypatch):
    """Verify database cli_main --init invokes Base.metadata.create_all and exits 0 on success."""
    monkeypatch.setattr(sys, "argv", ["database.py", "--init"])
    with (
        patch("app.database.database.verify_database_identity"),
        patch("app.database.database.Base.metadata.create_all") as mock_create,
        pytest.raises(SystemExit) as exc,
    ):
        cli_main()
    assert exc.value.code == 0
    mock_create.assert_called_once()


def test_database_cli_identity_mismatch_fails_closed(monkeypatch):
    """Verify database cli_main fails closed with exit code 1 on identity mismatch."""
    monkeypatch.setattr(sys, "argv", ["database.py", "--verify-only"])
    with patch("app.database.database.verify_database_identity", side_effect=DatabaseIdentityMismatchError("Host mismatch")):
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == 1


def test_operational_documentation_files_exist():
    """Verify all required operational documentation files exist and are non-empty."""
    repo_root = Path(__file__).resolve().parent.parent
    docs = [
        repo_root / ".github" / "BRANCH_PROTECTION.md",
        repo_root / "docs" / "rollback_runbook.md",
        repo_root / "docs" / "database_operations.md",
    ]
    for doc in docs:
        assert doc.is_file(), f"Expected documentation file {doc.name} must exist"
        assert doc.stat().st_size > 500, f"Documentation file {doc.name} must contain meaningful content"
