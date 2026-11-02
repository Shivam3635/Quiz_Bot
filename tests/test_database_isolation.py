"""Tests for database isolation safeguards and runtime identity verification."""

from unittest.mock import MagicMock, patch
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from app.config.settings import Settings
from app.database.database import (
    Base,
    DatabaseIdentityError,
    DatabaseIdentityMismatchError,
    init_db,
    verify_database_identity,
)


# ============================================================================
# 1. Configuration Validation Tests (Part B & Part D)
# ============================================================================

def test_staging_missing_expected_identity():
    """Staging configuration must fail validation if EXPECTED_DATABASE_IDENTITY is missing or empty."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY=None,
        )
    assert "EXPECTED_DATABASE_IDENTITY is required when ENVIRONMENT is 'staging'" in str(exc.value)

    with pytest.raises(ValidationError) as exc2:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="   ",
        )
    assert "EXPECTED_DATABASE_IDENTITY is required when ENVIRONMENT is 'staging'" in str(exc2.value)


def test_staging_missing_database_url():
    """Staging configuration must fail validation if DATABASE_URL is empty or default sqlite."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Dedicated non-default DATABASE_URL is required" in str(exc.value)


def test_staging_sqlite_rejection():
    """Staging configuration must reject SQLite schemes even if a custom file path is used."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="sqlite:///staging_test.db",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Staging database must use PostgreSQL. Found unsupported scheme: 'sqlite'" in str(exc.value)


def test_staging_malformed_url_rejection():
    """Staging configuration must reject malformed connection URLs."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="not_a_valid_connection_url",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Invalid or malformed DATABASE_URL" in str(exc.value)


def test_staging_matching_hostname_accepted():
    """Staging configuration accepts exact matching PostgreSQL hostname."""
    s = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.us-east-2.aws.neon.tech:5432/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.us-east-2.aws.neon.tech",
    )
    assert s.EXPECTED_DATABASE_IDENTITY == "ep-staging-123.us-east-2.aws.neon.tech"


def test_staging_mismatched_hostname_rejected():
    """Staging configuration must reject mismatched hostnames, including pooler vs direct endpoints."""
    # Direct endpoint expected, but pooled endpoint supplied
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123-pooler.us-east-2.aws.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.us-east-2.aws.neon.tech",
        )
    assert "Database identity mismatch" in str(exc.value)
    assert "ep-staging-123.us-east-2.aws.neon.tech" in str(exc.value)
    assert "ep-staging-123-pooler.us-east-2.aws.neon.tech" in str(exc.value)

    # Completely different production host supplied
    with pytest.raises(ValidationError) as exc2:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-production-999.us-east-2.aws.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.us-east-2.aws.neon.tech",
        )
    assert "Database identity mismatch" in str(exc2.value)


def test_production_works_without_expected_identity():
    """Production configuration must remain functional without EXPECTED_DATABASE_IDENTITY."""
    s = Settings(
        ENVIRONMENT="production",
        TELEGRAM_BOT_TOKEN="123456:prod_token",
        DATABASE_URL="postgresql://user:pass@ep-prod-999.neon.tech/neondb",
    )
    assert s.EXPECTED_DATABASE_IDENTITY is None
    # Runtime verification skips in production when no identity is configured
    res = verify_database_identity(app_settings=s)
    assert res["status"] == "skipped"


def test_development_and_testing_remain_compatible():
    """Development and testing modes must retain full default compatibility with SQLite."""
    with patch.dict("os.environ", {}, clear=True):
        dev_settings = Settings(_env_file=None, ENVIRONMENT="development")
        assert dev_settings.DATABASE_URL == "sqlite:///quizbotpro.db"
        res_dev = verify_database_identity(app_settings=dev_settings)
        assert res_dev["status"] == "skipped"

        test_settings = Settings(_env_file=None, ENVIRONMENT="testing")
        assert test_settings.DATABASE_URL == "sqlite:///quizbotpro.db"
        res_test = verify_database_identity(app_settings=test_settings)
        assert res_test["status"] == "skipped"


# ============================================================================
# 2. TLS & CA Trust Configuration Tests
# Note: Unit tests verify URL query parameter parsing and strict security validation.
# They do not simulate or prove a real TLS certificate handshake with remote servers.
# ============================================================================

def test_staging_accepts_verify_full_with_sslrootcert_system():
    """Staging accepts strict verification with sslmode=verify-full and sslrootcert=system."""
    s = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    assert s.EXPECTED_DATABASE_IDENTITY == "ep-staging-123.neon.tech"


def test_staging_accepts_verify_full_with_configured_ca_path():
    """Staging accepts strict verification with an explicit non-empty CA certificate path."""
    s = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=/etc/ssl/certs/custom-ca.pem",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    assert s.EXPECTED_DATABASE_IDENTITY == "ep-staging-123.neon.tech"


def test_staging_rejects_missing_sslmode():
    """Staging configuration must reject DATABASE_URL lacking sslmode, even if other parameters exist."""
    # Completely absent query parameters
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Missing required 'sslmode' parameter" in str(exc.value)

    # Other parameters present, but sslmode missing
    with pytest.raises(ValidationError) as exc2:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?connect_timeout=10&sslrootcert=system",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Missing required 'sslmode' parameter" in str(exc2.value)


def test_staging_rejects_require_sslmode():
    """Staging configuration must explicitly reject sslmode='require'."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=require&sslrootcert=system",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Invalid or insecure sslmode 'require' is not permitted" in str(exc.value)
    assert "Staging strictly requires 'sslmode=verify-full'" in str(exc.value)


def test_staging_rejects_verify_ca_sslmode():
    """Staging configuration must explicitly reject sslmode='verify-ca'."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-ca&sslrootcert=system",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Invalid or insecure sslmode 'verify-ca' is not permitted" in str(exc.value)
    assert "Staging strictly requires 'sslmode=verify-full'" in str(exc.value)


def test_staging_rejects_disable_allow_prefer_sslmodes():
    """Staging configuration must reject unencrypted or insecure sslmodes (disable, allow, prefer)."""
    for insecure_mode in ("disable", "allow", "prefer"):
        with pytest.raises(ValidationError) as exc:
            Settings(
                ENVIRONMENT="staging",
                TELEGRAM_BOT_TOKEN="123456:valid_token",
                DATABASE_URL=f"postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode={insecure_mode}&sslrootcert=system",
                STAGING_ALLOWED_USER_IDS="111, 222",
                EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
            )
        assert f"Invalid or insecure sslmode '{insecure_mode}' is not permitted" in str(exc.value)
        assert "Staging strictly requires 'sslmode=verify-full'" in str(exc.value)


def test_staging_rejects_missing_or_empty_sslrootcert():
    """When using sslmode='verify-full', missing or empty sslrootcert must be rejected."""
    # Missing sslrootcert
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Missing required 'sslrootcert' parameter" in str(exc.value)

    # Empty sslrootcert parameter
    with pytest.raises(ValidationError) as exc2:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert ("Missing required 'sslrootcert' parameter" in str(exc2.value) or
            "Empty or malformed 'sslrootcert' parameter" in str(exc2.value))


def test_staging_rejects_duplicate_or_conflicting_ssl_parameters():
    """Staging configuration must reject duplicate or conflicting sslmode/sslrootcert parameters safely."""
    # Conflicting / duplicate sslmode
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslmode=disable&sslrootcert=system",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Conflicting or duplicate 'sslmode' parameters" in str(exc.value)

    # Conflicting / duplicate sslrootcert
    with pytest.raises(ValidationError) as exc2:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token",
            DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system&sslrootcert=/etc/ca.crt",
            STAGING_ALLOWED_USER_IDS="111, 222",
            EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
        )
    assert "Conflicting or duplicate 'sslrootcert' parameters" in str(exc2.value)


def test_production_development_and_testing_unaffected_by_staging_ssl_rules():
    """Production, development, and testing modes must not impose staging TLS query constraints."""
    prod = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        TELEGRAM_BOT_TOKEN="123456:prod_token",
        DATABASE_URL="postgresql://user:pass@ep-prod-999.neon.tech/neondb?sslmode=require",
    )
    assert prod.ENVIRONMENT == "production"

    prod_no_ssl = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        TELEGRAM_BOT_TOKEN="123456:prod_token",
        DATABASE_URL="postgresql://user:pass@ep-prod-999.neon.tech/neondb",
    )
    assert prod_no_ssl.ENVIRONMENT == "production"

    with patch.dict("os.environ", {}, clear=True):
        dev = Settings(_env_file=None, ENVIRONMENT="development")
        assert dev.DATABASE_URL == "sqlite:///quizbotpro.db"

        test_env = Settings(_env_file=None, ENVIRONMENT="testing")
        assert test_env.DATABASE_URL == "sqlite:///quizbotpro.db"


# ============================================================================
# 3. Runtime Database Identity Verification & Single Connection DDL Tests (Part C)
# ============================================================================

def test_runtime_verification_succeeds_with_valid_mocked_identity():
    """Runtime verification executes read-only query and succeeds when identity matches."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )

    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    # SELECT current_database(), current_user, version()
    mock_cursor.fetchone.return_value = ("neondb_staging", "staging_owner", "PostgreSQL 16.3 on x86_64")
    mock_conn.execute.return_value = mock_cursor

    # Mock DBAPI dsn_parameters
    mock_raw_conn = MagicMock()
    mock_dbapi = MagicMock()
    mock_dbapi.get_dsn_parameters.return_value = {"host": "ep-staging-123.neon.tech", "dbname": "neondb_staging"}
    mock_raw_conn.dbapi_connection = mock_dbapi
    mock_conn.connection = mock_raw_conn

    mock_engine.connect.return_value = mock_conn

    res = verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert res["status"] == "verified"
    assert res["expected_identity"] == "ep-staging-123.neon.tech"
    assert res["current_database"] == "neondb_staging"
    assert res["current_user"] == "staging_owner"
    assert "PostgreSQL 16.3" in res["version"]
    assert res["driver_verified"] is True

    mock_conn.close.assert_called_once()


def test_runtime_verification_failure_prevents_init_db():
    """When runtime verification fails, init_db() must raise DatabaseIdentityError without creating tables."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )

    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_conn
    mock_engine.begin.return_value = mock_context

    with patch("app.database.database.get_settings", return_value=staging_settings), \
         patch("app.database.database.verify_database_identity", side_effect=DatabaseIdentityMismatchError("Host mismatch")), \
         patch("app.database.database.Base.metadata.create_all") as mock_create_all:

        with pytest.raises(DatabaseIdentityMismatchError) as exc:
            init_db(target_engine=mock_engine, app_settings=staging_settings)

        assert "Host mismatch" in str(exc.value)
        mock_create_all.assert_not_called()
        mock_context.__exit__.assert_called_once()
        mock_engine.dispose.assert_called_once()


def test_connection_closed_and_engine_disposed_on_verification_failure():
    """Verification failure must cleanly close the connection and dispose of the engine."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )

    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    mock_conn = MagicMock()
    mock_raw = MagicMock()
    mock_dbapi = MagicMock()
    mock_dbapi.get_dsn_parameters.return_value = {"host": "ep-staging-123.neon.tech"}
    mock_raw.dbapi_connection = mock_dbapi
    mock_conn.connection = mock_raw
    mock_conn.execute.side_effect = RuntimeError("Database unreachable or network error")
    mock_engine.connect.return_value = mock_conn

    with pytest.raises(DatabaseIdentityError) as exc:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)

    assert "Failed to execute database identity query" in str(exc.value)
    mock_conn.close.assert_called()
    mock_engine.dispose.assert_called()


def test_no_schema_creation_occurs_before_verification():
    """Verify strictly that verify_database_identity executes before Base.metadata.create_all."""
    execution_order = []

    def mock_verify(**kwargs):
        execution_order.append("verify")

    def mock_create_all(bind):
        execution_order.append("create_all")

    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("sqlite:///:memory:")
    mock_conn = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_conn
    mock_engine.begin.return_value = mock_context

    with patch("app.database.database.verify_database_identity", side_effect=mock_verify), \
         patch("app.database.database.Base.metadata.create_all", side_effect=mock_create_all):

        init_db(target_engine=mock_engine)

    assert execution_order == ["verify", "create_all"], "verify_database_identity must execute before Base.metadata.create_all"
    mock_engine.begin.assert_called_once()
    mock_context.__exit__.assert_called_once()


def test_safeguard_no_real_database_connection_in_init_db():
    """Test safeguard: verify that init_db with mock engine never calls raw_connection on real engine."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_context = MagicMock()
    mock_engine.begin.return_value = mock_context

    with patch("app.database.database.verify_database_identity") as mock_verify, \
         patch("app.database.database.Base.metadata.create_all") as mock_create_all, \
         patch("app.database.database.engine") as mock_module_engine:

        init_db(target_engine=mock_engine, app_settings=staging_settings)

        # Real module-level engine must not be touched when target_engine is provided
        mock_module_engine.begin.assert_not_called()
        mock_module_engine.connect.assert_not_called()
        mock_module_engine.raw_connection.assert_not_called()
        mock_engine.begin.assert_called_once()


def test_staging_uses_same_connection_for_verification_and_create_all():
    """Verify that init_db executes verification and Base.metadata.create_all on the exact same physical connection."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )

    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    mock_conn = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_conn
    mock_engine.begin.return_value = mock_context

    captured_verification_conn = None
    captured_create_all_conn = None

    def spy_verify(conn=None, **kwargs):
        nonlocal captured_verification_conn
        captured_verification_conn = conn

    def spy_create_all(bind=None, **kwargs):
        nonlocal captured_create_all_conn
        captured_create_all_conn = bind

    with patch("app.database.database.verify_database_identity", side_effect=spy_verify), \
         patch("app.database.database.Base.metadata.create_all", side_effect=spy_create_all):

        init_db(target_engine=mock_engine, app_settings=staging_settings)

    assert captured_verification_conn is not None
    assert captured_create_all_conn is not None
    assert captured_verification_conn is mock_conn
    assert captured_create_all_conn is mock_conn
    assert captured_verification_conn is captured_create_all_conn, "Verification and create_all must share the exact physical connection"
    mock_context.__exit__.assert_called_once()


def test_staging_verification_failure_rolls_back_and_prevents_schema_creation():
    """Verify that a verification error inside engine.begin() prevents create_all and disposes the engine."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )

    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    mock_conn = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_conn
    mock_engine.begin.return_value = mock_context

    with patch("app.database.database.verify_database_identity", side_effect=DatabaseIdentityMismatchError("Host mismatch")), \
         patch("app.database.database.Base.metadata.create_all") as mock_create_all:

        with pytest.raises(DatabaseIdentityMismatchError):
            init_db(target_engine=mock_engine, app_settings=staging_settings)

        mock_create_all.assert_not_called()
        mock_engine.dispose.assert_called_once()
        # Context manager exit handles rollback on exception
        mock_context.__exit__.assert_called_once()


def test_production_initialization_behaviour_remains_unchanged():
    """Production initialization runs smoothly on default SQLite engine without requiring expected identity."""
    prod_settings = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        TELEGRAM_BOT_TOKEN="123456:prod_token",
        DATABASE_URL="sqlite:///:memory:",
    )
    test_engine = create_engine("sqlite:///:memory:")

    init_db(target_engine=test_engine, app_settings=prod_settings)

    # Verify tables actually exist on test_engine
    with test_engine.connect() as conn:
        res = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        table_names = [row[0] for row in res]
        assert "users" in table_names
        assert "quiz_sets" in table_names


def test_identity_query_returns_no_row_fails_closed():
    """If identity query returns no row, verification must fail closed and raise DatabaseIdentityError."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None  # No row returned
    mock_conn.execute.return_value = mock_cursor

    mock_raw_conn = MagicMock()
    mock_dbapi = MagicMock()
    mock_dbapi.get_dsn_parameters.return_value = {"host": "ep-staging-123.neon.tech"}
    mock_raw_conn.dbapi_connection = mock_dbapi
    mock_conn.connection = mock_raw_conn
    mock_engine.connect.return_value = mock_conn

    with pytest.raises(DatabaseIdentityError) as exc:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)

    assert "empty or unusable session metadata" in str(exc.value)
    mock_conn.close.assert_called_once()


def test_identity_query_exception_fails_closed_with_chaining():
    """If the identity query raises a database exception, verify_database_identity must raise with exception chaining."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    mock_conn = MagicMock()
    original_err = RuntimeError("Connection dropped by remote host during SELECT")
    mock_conn.execute.side_effect = original_err

    mock_raw_conn = MagicMock()
    mock_dbapi = MagicMock()
    mock_dbapi.get_dsn_parameters.return_value = {"host": "ep-staging-123.neon.tech"}
    mock_raw_conn.dbapi_connection = mock_dbapi
    mock_conn.connection = mock_raw_conn
    mock_engine.connect.return_value = mock_conn

    with pytest.raises(DatabaseIdentityError) as exc:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)

    assert "Failed to execute database identity query" in str(exc.value)
    assert exc.value.__cause__ is original_err
    mock_conn.close.assert_called_once()


def test_driver_dsn_parameters_missing_or_unusable_fails_closed():
    """In staging, missing or unusable driver DSN hostname must fail closed."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    # Case 1: get_dsn_parameters returns non-dict
    mock_conn1 = MagicMock()
    mock_raw1 = MagicMock()
    mock_dbapi1 = MagicMock()
    mock_dbapi1.get_dsn_parameters.return_value = "invalid_non_dict"
    mock_raw1.dbapi_connection = mock_dbapi1
    mock_conn1.connection = mock_raw1
    mock_engine.connect.return_value = mock_conn1

    with pytest.raises(DatabaseIdentityError) as exc1:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert "failed to provide valid DSN parameter mapping" in str(exc1.value)

    # Case 2: get_dsn_parameters missing 'host'
    mock_conn2 = MagicMock()
    mock_raw2 = MagicMock()
    mock_dbapi2 = MagicMock()
    mock_dbapi2.get_dsn_parameters.return_value = {"port": "5432"}  # missing host
    mock_raw2.dbapi_connection = mock_dbapi2
    mock_conn2.connection = mock_raw2
    mock_engine.connect.return_value = mock_conn2

    with pytest.raises(DatabaseIdentityError) as exc2:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert "missing required 'host' attribute" in str(exc2.value)


def test_driver_verification_missing_dbapi_connection_fails_closed():
    """In staging, missing raw connection or DBAPI connection must fail closed."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    # Case 1: active_conn.connection is None
    mock_conn1 = MagicMock()
    mock_conn1.connection = None
    mock_engine.connect.return_value = mock_conn1

    with pytest.raises(DatabaseIdentityError) as exc1:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert "Raw database connection is unavailable" in str(exc1.value)

    # Case 2: raw_conn.dbapi_connection is None
    mock_conn2 = MagicMock()
    mock_raw2 = MagicMock()
    mock_raw2.dbapi_connection = None
    mock_conn2.connection = mock_raw2
    mock_engine.connect.return_value = mock_conn2

    with pytest.raises(DatabaseIdentityError) as exc2:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert "DBAPI connection is unavailable" in str(exc2.value)


def test_driver_verification_unsupported_driver_inspection_fails_closed():
    """In staging, an unsupported PostgreSQL driver or driver lacking inspection mechanism must fail closed."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )

    # Case 1: Configured driver is explicitly unsupported (e.g. pg8000)
    mock_engine1 = MagicMock(spec=Engine)
    mock_engine1.url = make_url("postgresql+pg8000://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn1 = MagicMock()
    mock_raw1 = MagicMock()
    mock_dbapi1 = MagicMock()
    mock_raw1.dbapi_connection = mock_dbapi1
    mock_conn1.connection = mock_raw1
    mock_engine1.connect.return_value = mock_conn1

    with pytest.raises(DatabaseIdentityError) as exc1:
        verify_database_identity(target_engine=mock_engine1, app_settings=staging_settings)
    assert "is unsupported" in str(exc1.value)

    # Case 2: Configured driver is asyncpg (asynchronous driver rejected for synchronous architecture)
    mock_engine2 = MagicMock(spec=Engine)
    mock_engine2.url = make_url("postgresql+asyncpg://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn2 = MagicMock()
    mock_raw2 = MagicMock()
    mock_dbapi2 = MagicMock()
    mock_raw2.dbapi_connection = mock_dbapi2
    mock_conn2.connection = mock_raw2
    mock_engine2.connect.return_value = mock_conn2

    with pytest.raises(DatabaseIdentityError) as exc2:
        verify_database_identity(target_engine=mock_engine2, app_settings=staging_settings)
    assert "is unsupported" in str(exc2.value)

    # Case 3: Driver is psycopg2, but dbapi connection does not expose get_dsn_parameters
    mock_engine3 = MagicMock(spec=Engine)
    mock_engine3.url = make_url("postgresql+psycopg2://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn3 = MagicMock()
    mock_raw3 = MagicMock()
    mock_raw3.dbapi_connection = object()
    mock_conn3.connection = mock_raw3
    mock_engine3.connect.return_value = mock_conn3

    with pytest.raises(DatabaseIdentityError) as exc3:
        verify_database_identity(target_engine=mock_engine3, app_settings=staging_settings)
    assert "does not expose the required 'get_dsn_parameters' inspection method" in str(exc3.value)

    # Case 4: Driver is psycopg (psycopg 3), but dbapi connection does not expose info or pgconn
    mock_engine4 = MagicMock(spec=Engine)
    mock_engine4.url = make_url("postgresql+psycopg://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn4 = MagicMock()
    mock_raw4 = MagicMock()
    mock_raw4.dbapi_connection = object()
    mock_conn4.connection = mock_raw4
    mock_engine4.connect.return_value = mock_conn4

    with pytest.raises(DatabaseIdentityError) as exc4:
        verify_database_identity(target_engine=mock_engine4, app_settings=staging_settings)
    assert "does not expose documented connection information API" in str(exc4.value)


def test_driver_verification_malformed_dsn_response_fails_closed():
    """In staging, malformed DSN parameter mappings or unexpected driver errors must fail closed."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql+psycopg2://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    # Case 1: get_dsn_parameters returns non-dict integer
    mock_conn1 = MagicMock()
    mock_raw1 = MagicMock()
    mock_dbapi1 = MagicMock()
    mock_dbapi1.get_dsn_parameters.return_value = 12345
    mock_raw1.dbapi_connection = mock_dbapi1
    mock_conn1.connection = mock_raw1
    mock_engine.connect.return_value = mock_conn1

    with pytest.raises(DatabaseIdentityError) as exc1:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert "failed to provide valid DSN parameter mapping" in str(exc1.value)

    # Case 2: get_dsn_parameters raises unexpected exception
    mock_conn2 = MagicMock()
    mock_raw2 = MagicMock()
    mock_dbapi2 = MagicMock()
    mock_dbapi2.get_dsn_parameters.side_effect = RuntimeError("Low-level libpq connection corrupted")
    mock_raw2.dbapi_connection = mock_dbapi2
    mock_conn2.connection = mock_raw2
    mock_engine.connect.return_value = mock_conn2

    with pytest.raises(DatabaseIdentityError) as exc2:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert "Failed to inspect database driver DSN parameters" in str(exc2.value)


def test_driver_verification_empty_hostname_fails_closed():
    """In staging, driver returning an empty or whitespace-only hostname must fail closed."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql+psycopg2://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    # Case 1: psycopg2 returns empty string host
    mock_conn1 = MagicMock()
    mock_raw1 = MagicMock()
    mock_dbapi1 = MagicMock()
    mock_dbapi1.get_dsn_parameters.return_value = {"host": ""}
    mock_raw1.dbapi_connection = mock_dbapi1
    mock_conn1.connection = mock_raw1
    mock_engine.connect.return_value = mock_conn1

    with pytest.raises(DatabaseIdentityError) as exc1:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert "empty hostname" in str(exc1.value)

    # Case 2: psycopg2 returns whitespace-only host
    mock_conn2 = MagicMock()
    mock_raw2 = MagicMock()
    mock_dbapi2 = MagicMock()
    mock_dbapi2.get_dsn_parameters.return_value = {"host": "   "}
    mock_raw2.dbapi_connection = mock_dbapi2
    mock_conn2.connection = mock_raw2
    mock_engine.connect.return_value = mock_conn2

    with pytest.raises(DatabaseIdentityError) as exc2:
        verify_database_identity(target_engine=mock_engine, app_settings=staging_settings)
    assert "empty hostname" in str(exc2.value)

    # Case 3: psycopg (psycopg 3) returns empty string host
    mock_engine3 = MagicMock(spec=Engine)
    mock_engine3.url = make_url("postgresql+psycopg://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn3 = MagicMock()
    mock_raw3 = MagicMock()
    mock_dbapi3 = MagicMock(spec=["info"])
    mock_info = MagicMock()
    mock_info.host = ""
    mock_dbapi3.info = mock_info
    mock_raw3.dbapi_connection = mock_dbapi3
    mock_conn3.connection = mock_raw3
    mock_engine3.connect.return_value = mock_conn3

    with pytest.raises(DatabaseIdentityError) as exc3:
        verify_database_identity(target_engine=mock_engine3, app_settings=staging_settings)
    assert "empty hostname" in str(exc3.value)


def test_driver_verification_mismatched_hostname_fails_closed():
    """In staging, driver returning a mismatched hostname must raise DatabaseIdentityMismatchError."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )

    # Case 1: psycopg2 driver connected to different host (e.g. production host)
    mock_engine1 = MagicMock(spec=Engine)
    mock_engine1.url = make_url("postgresql+psycopg2://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn1 = MagicMock()
    mock_raw1 = MagicMock()
    mock_dbapi1 = MagicMock()
    mock_dbapi1.get_dsn_parameters.return_value = {"host": "ep-prod-999.neon.tech"}
    mock_raw1.dbapi_connection = mock_dbapi1
    mock_conn1.connection = mock_raw1
    mock_engine1.connect.return_value = mock_conn1

    with pytest.raises(DatabaseIdentityMismatchError) as exc1:
        verify_database_identity(target_engine=mock_engine1, app_settings=staging_settings)
    assert "Database driver identity mismatch" in str(exc1.value)
    assert "ep-prod-999.neon.tech" in str(exc1.value)
    assert "ep-staging-123.neon.tech" in str(exc1.value)

    # Case 2: psycopg (psycopg 3) driver connected to different host
    mock_engine2 = MagicMock(spec=Engine)
    mock_engine2.url = make_url("postgresql+psycopg://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn2 = MagicMock()
    mock_raw2 = MagicMock()
    mock_dbapi2 = MagicMock(spec=["info"])
    mock_info = MagicMock()
    mock_info.host = "ep-staging-pooler.neon.tech"
    mock_dbapi2.info = mock_info
    mock_raw2.dbapi_connection = mock_dbapi2
    mock_conn2.connection = mock_raw2
    mock_engine2.connect.return_value = mock_conn2

    with pytest.raises(DatabaseIdentityMismatchError) as exc2:
        verify_database_identity(target_engine=mock_engine2, app_settings=staging_settings)
    assert "Database driver identity mismatch" in str(exc2.value)


def test_driver_verification_matching_hostname_sets_driver_verified_true():
    """In staging, matching driver hostname across supported drivers sets driver_verified=True."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )

    # Case 1: psycopg2 driver matching host
    mock_engine1 = MagicMock(spec=Engine)
    mock_engine1.url = make_url("postgresql+psycopg2://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn1 = MagicMock()
    mock_cursor1 = MagicMock()
    mock_cursor1.fetchone.return_value = ("staging_db", "staging_user", "PostgreSQL 16")
    mock_conn1.execute.return_value = mock_cursor1
    mock_raw1 = MagicMock()
    mock_dbapi1 = MagicMock()
    mock_dbapi1.get_dsn_parameters.return_value = {"host": "ep-staging-123.neon.tech"}
    mock_raw1.dbapi_connection = mock_dbapi1
    mock_conn1.connection = mock_raw1
    mock_engine1.connect.return_value = mock_conn1

    res1 = verify_database_identity(target_engine=mock_engine1, app_settings=staging_settings)
    assert res1["status"] == "verified"
    assert res1["driver_verified"] is True
    assert res1["expected_identity"] == "ep-staging-123.neon.tech"

    # Case 2: psycopg (psycopg 3) driver matching host via info.host
    mock_engine2 = MagicMock(spec=Engine)
    mock_engine2.url = make_url("postgresql+psycopg://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn2 = MagicMock()
    mock_cursor2 = MagicMock()
    mock_cursor2.fetchone.return_value = ("staging_db", "staging_user", "PostgreSQL 16")
    mock_conn2.execute.return_value = mock_cursor2
    mock_raw2 = MagicMock()
    mock_dbapi2 = MagicMock(spec=["info"])
    mock_info = MagicMock()
    mock_info.host = "ep-staging-123.neon.tech"
    mock_dbapi2.info = mock_info
    mock_raw2.dbapi_connection = mock_dbapi2
    mock_conn2.connection = mock_raw2
    mock_engine2.connect.return_value = mock_conn2

    res2 = verify_database_identity(target_engine=mock_engine2, app_settings=staging_settings)
    assert res2["status"] == "verified"
    assert res2["driver_verified"] is True

    # Case 3: psycopg (psycopg 3) driver matching host via pgconn.host
    mock_engine3 = MagicMock(spec=Engine)
    mock_engine3.url = make_url("postgresql+psycopg://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    mock_conn3 = MagicMock()
    mock_cursor3 = MagicMock()
    mock_cursor3.fetchone.return_value = ("staging_db", "staging_user", "PostgreSQL 16")
    mock_conn3.execute.return_value = mock_cursor3
    mock_raw3 = MagicMock()
    mock_dbapi3 = MagicMock(spec=["pgconn"])
    mock_pgconn = MagicMock()
    mock_pgconn.host = "ep-staging-123.neon.tech"
    mock_dbapi3.pgconn = mock_pgconn
    mock_raw3.dbapi_connection = mock_dbapi3
    mock_conn3.connection = mock_raw3
    mock_engine3.connect.return_value = mock_conn3

    res3 = verify_database_identity(target_engine=mock_engine3, app_settings=staging_settings)
    assert res3["status"] == "verified"
    assert res3["driver_verified"] is True


def test_schema_creation_failure_rolls_back_and_disposes_in_staging():
    """If Base.metadata.create_all fails after successful verification, transaction exits and engine is disposed in staging."""
    staging_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token",
        DATABASE_URL="postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging-123.neon.tech",
    )
    mock_engine = MagicMock(spec=Engine)
    mock_engine.url = make_url("postgresql://user:pass@ep-staging-123.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")

    mock_conn = MagicMock()
    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_conn
    mock_engine.begin.return_value = mock_context

    with patch("app.database.database.verify_database_identity", return_value={"status": "verified"}), \
         patch("app.database.database.Base.metadata.create_all", side_effect=RuntimeError("DDL execution failed")):

        with pytest.raises(RuntimeError) as exc:
            init_db(target_engine=mock_engine, app_settings=staging_settings)

        assert "DDL execution failed" in str(exc.value)
        mock_context.__exit__.assert_called_once()
        mock_engine.dispose.assert_called_once()


def test_transaction_rollback_actually_occurs_on_failure_in_memory_db():
    """Verify with a real in-memory SQLite database that failed initialization transactions genuinely roll back uncommitted DDL/data."""
    test_engine = create_engine("sqlite:///:memory:")

    # Create baseline table outside of the failing transaction
    with test_engine.begin() as conn:
        conn.execute(text("CREATE TABLE test_rollback (id INTEGER PRIMARY KEY, note TEXT);"))

    # Execute a transaction that inserts a row and then fails
    with pytest.raises(RuntimeError):
        with test_engine.begin() as conn:
            conn.execute(text("INSERT INTO test_rollback (id, note) VALUES (1, 'should_rollback');"))
            # Trigger intentional failure before commit
            raise RuntimeError("Intentional failure to trigger rollback")

    # Verify that the uncommitted row was indeed rolled back
    with test_engine.connect() as conn:
        rows = conn.execute(text("SELECT id, note FROM test_rollback;")).fetchall()
        assert len(rows) == 0, f"Expected 0 rows after rollback, found {len(rows)}"
