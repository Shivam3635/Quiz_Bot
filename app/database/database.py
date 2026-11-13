"""SQLAlchemy database engine and session management."""

import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from app.config.settings import Settings, get_settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class DatabaseIdentityError(Exception):
    """Base exception for database identity and isolation verification failures."""
    pass


class DatabaseIdentityMismatchError(DatabaseIdentityError):
    """Raised when the connected database host does not match the expected identity."""
    pass


# Standard locations for trusted root CA certificate bundles across Linux distributions and macOS
STANDARD_CA_BUNDLE_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",                 # Debian, Ubuntu, Render
    "/etc/pki/tls/certs/ca-bundle.crt",                   # CentOS, RHEL, Fedora, Alma, Rocky
    "/etc/ssl/ca-bundle.pem",                             # openSUSE, SUSE
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # RHEL 7+, CentOS 7+
    "/etc/ssl/cert.pem",                                  # Alpine Linux, macOS, FreeBSD
)


def resolve_ca_bundle_path() -> str | None:
    """Resolve a trusted root CA certificate bundle path.

    PostgreSQL libpq's `sslrootcert=system` delegates to OpenSSL's
    `X509_STORE_set_default_paths()`. In precompiled binary wheels
    (such as `psycopg2-binary` built on manylinux), OpenSSL's compiled-in
    OPENSSLDIR typically points to `/etc/pki/tls`, which does not exist
    on Debian/Ubuntu/Render (`/etc/ssl/certs/ca-certificates.crt`) or Windows.
    This causes certificate verification to fail with:
    `SSL error: certificate verify failed` even when the server certificate is valid.

    This function locates the appropriate trusted CA bundle by checking:
    1. Explicit `SSL_CERT_FILE` environment variable if set and exists.
    2. Explicit `PGSSLROOTCERT` environment variable if set and exists.
    3. Standard Linux / Unix system CA certificate bundle paths.
    4. Mozilla root CA bundle via `certifi` (guaranteed fallback in Python).

    Returns:
        Absolute path to an existing, non-empty CA certificate bundle file,
        or None if no bundle could be located.
    """
    # 1. Respect explicit SSL_CERT_FILE
    ssl_cert_file = os.environ.get("SSL_CERT_FILE", "").strip()
    if ssl_cert_file and os.path.isfile(ssl_cert_file) and os.path.getsize(ssl_cert_file) > 0:
        return ssl_cert_file

    # 2. Respect explicit PGSSLROOTCERT
    pg_ssl_root_cert = os.environ.get("PGSSLROOTCERT", "").strip()
    if pg_ssl_root_cert and os.path.isfile(pg_ssl_root_cert) and os.path.getsize(pg_ssl_root_cert) > 0:
        return pg_ssl_root_cert

    # 3. Check standard system CA certificate bundle paths
    for system_path in STANDARD_CA_BUNDLE_PATHS:
        if os.path.isfile(system_path) and os.path.getsize(system_path) > 0:
            return system_path

    # 4. Fallback to certifi's Mozilla root CA bundle
    try:
        import certifi
        certifi_path = certifi.where()
        if os.path.isfile(certifi_path) and os.path.getsize(certifi_path) > 0:
            return certifi_path
    except ImportError:
        pass

    return None


def configure_ssl_certificate_bundle(app_settings: Settings | None = None) -> str | None:
    """Configure runtime environment and return resolved CA certificate bundle path.

    Ensures that `SSL_CERT_FILE` is populated in `os.environ` so that any
    driver or libpq call with `sslrootcert=system` can locate trusted CA
    certificates in containerized and cloud runtimes (e.g. Render).

    In staging mode, failing to locate a valid root CA bundle aborts startup
    by raising DatabaseIdentityError. Never weakens TLS verification or falls back
    to unverified modes.
    """
    current_settings = app_settings or get_settings()
    ca_bundle = resolve_ca_bundle_path()
    if ca_bundle:
        if "SSL_CERT_FILE" not in os.environ or not os.environ["SSL_CERT_FILE"].strip():
            os.environ["SSL_CERT_FILE"] = ca_bundle
            logger.debug("Configured SSL_CERT_FILE=%s for OpenSSL/libpq certificate verification.", ca_bundle)
        else:
            logger.debug("Using existing SSL_CERT_FILE=%s", os.environ["SSL_CERT_FILE"])
    else:
        if current_settings.ENVIRONMENT == "staging":
            raise DatabaseIdentityError(
                "Staging startup aborted: Unable to locate a valid root CA certificate bundle for strict TLS verification (sslmode=verify-full)."
            )
        logger.warning(
            "Could not locate a trusted CA certificate bundle on this system. "
            "TLS certificate verification with 'sslrootcert=system' may fail."
        )
    return ca_bundle


settings = get_settings()

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

# Connect args needed for SQLite concurrency
connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}

# For PostgreSQL connections, configure CA certificate bundle to ensure reliable TLS verification
if not db_url.startswith("sqlite"):
    ca_bundle = configure_ssl_certificate_bundle(settings)
    # If the connection string specifies sslrootcert=system, passing the explicit CA bundle
    # in connect_args ensures libpq directly loads the certificate file across all platforms
    # without relying exclusively on OpenSSL's compiled-in OPENSSLDIR search paths.
    if ca_bundle and "sslrootcert=system" in db_url:
        connect_args["sslrootcert"] = ca_bundle

engine = create_engine(
    db_url,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
SessionLocal = scoped_session(SessionFactory)
Base = declarative_base()


SUPPORTED_SYNCHRONOUS_POSTGRES_DRIVERS = ("psycopg2", "psycopg")


def _extract_driver_hostname(
    active_conn: Connection,
    current_engine: Engine | None = None,
) -> str:
    """Extract and return the connected hostname directly from the underlying DBAPI driver.

    The application uses synchronous SQLAlchemy (`create_engine`, `Engine.begin`).
    Only synchronous PostgreSQL drivers declared and used by this project (`psycopg2`
    and `psycopg` / psycopg 3) are supported for staging identity verification.

    In staging, driver-level hostname inspection is mandatory and fails closed (raises
    DatabaseIdentityError) if:
    1. The raw connection or DBAPI connection is unavailable.
    2. The configured driver is unknown or unsupported (e.g. asyncpg, pg8000).
    3. The active driver does not expose its documented hostname inspection mechanism.
    4. The driver inspection returns malformed data, a missing host, or an empty hostname.

    Note: Hostname matching alone verifies the network endpoint address, but does not
    independently prove Neon project identity. Complete project isolation relies on separate
    Neon project IDs, distinct credentials, and isolated environment configurations.
    """
    raw_conn = getattr(active_conn, "connection", None)
    if raw_conn is None:
        raise DatabaseIdentityError(
            "Database isolation error: Raw database connection is unavailable for driver verification."
        )

    # In SQLAlchemy, active_conn.connection is typically a _ConnectionFairy wrapping dbapi_connection
    if hasattr(raw_conn, "dbapi_connection"):
        dbapi_conn = raw_conn.dbapi_connection
    else:
        dbapi_conn = raw_conn

    if dbapi_conn is None:
        raise DatabaseIdentityError(
            "Database isolation error: DBAPI connection is unavailable for driver verification."
        )

    # Identify the active driver using SQLAlchemy's configured dialect/driver information
    driver_name = ""
    if current_engine is not None and hasattr(current_engine, "dialect"):
        dialect_driver = getattr(current_engine.dialect, "driver", None)
        if isinstance(dialect_driver, str) and dialect_driver:
            driver_name = dialect_driver.lower().strip()

    if not driver_name and current_engine is not None:
        url = getattr(current_engine, "url", None)
        if url is not None:
            drivername = getattr(url, "drivername", "")
            if isinstance(drivername, str) and drivername.lower() in ("postgresql", "postgres"):
                driver_name = "psycopg2"
            elif hasattr(url, "get_driver_name"):
                name = url.get_driver_name()
                if isinstance(name, str) and name:
                    driver_name = name.lower().strip()

    if not driver_name:
        conn_engine = getattr(active_conn, "engine", None)
        if conn_engine is not None and conn_engine is not current_engine:
            if hasattr(conn_engine, "dialect"):
                dialect_driver = getattr(conn_engine.dialect, "driver", None)
                if isinstance(dialect_driver, str) and dialect_driver:
                    driver_name = dialect_driver.lower().strip()
            if not driver_name and hasattr(conn_engine, "url"):
                url = conn_engine.url
                if url is not None:
                    drivername = getattr(url, "drivername", "")
                    if isinstance(drivername, str) and drivername.lower() in ("postgresql", "postgres"):
                        driver_name = "psycopg2"
                    elif hasattr(url, "get_driver_name"):
                        name = url.get_driver_name()
                        if isinstance(name, str) and name:
                            driver_name = name.lower().strip()

    # Reject unknown or unsupported drivers
    if not driver_name or driver_name not in SUPPORTED_SYNCHRONOUS_POSTGRES_DRIVERS:
        raise DatabaseIdentityError(
            f"Configured PostgreSQL driver '{driver_name or 'unknown'}' is unsupported. "
            f"Only synchronous PostgreSQL drivers {SUPPORTED_SYNCHRONOUS_POSTGRES_DRIVERS} are supported."
        )

    # Driver-specific hostname extraction:
    # 1. psycopg2: require documented DSN inspection method (get_dsn_parameters)
    if driver_name == "psycopg2":
        if not (hasattr(dbapi_conn, "get_dsn_parameters") and callable(getattr(dbapi_conn, "get_dsn_parameters"))):
            raise DatabaseIdentityError(
                "Configured PostgreSQL driver 'psycopg2' does not expose the required 'get_dsn_parameters' inspection method."
            )
        try:
            dsn_params = dbapi_conn.get_dsn_parameters()
        except Exception as dsn_err:
            raise DatabaseIdentityError(
                f"Failed to inspect database driver DSN parameters: {dsn_err}"
            ) from dsn_err

        if not isinstance(dsn_params, dict):
            raise DatabaseIdentityError(
                "Database driver failed to provide valid DSN parameter mapping."
            )

        raw_host = dsn_params.get("host")
        if raw_host is None:
            raise DatabaseIdentityError(
                "Database driver DSN parameters missing required 'host' attribute."
            )
        if not isinstance(raw_host, str):
            raise DatabaseIdentityError(
                "Database driver DSN parameter 'host' must be a valid string."
            )
        host = raw_host.strip()
        if not host:
            raise DatabaseIdentityError(
                "Database driver returned empty hostname."
            )
        return host

    # 2. psycopg (psycopg 3): use documented connection information API (info.host / pgconn.host)
    if driver_name == "psycopg":
        raw_host = None
        if hasattr(dbapi_conn, "info") and hasattr(getattr(dbapi_conn, "info"), "host"):
            try:
                info_obj = getattr(dbapi_conn, "info")
                raw_host = getattr(info_obj, "host", None)
                if callable(raw_host):
                    raw_host = raw_host()
            except Exception as info_err:
                raise DatabaseIdentityError(
                    f"Failed to inspect psycopg driver info: {info_err}"
                ) from info_err
        elif hasattr(dbapi_conn, "pgconn") and hasattr(getattr(dbapi_conn, "pgconn"), "host"):
            try:
                pgconn_obj = getattr(dbapi_conn, "pgconn")
                raw_host = getattr(pgconn_obj, "host", None)
                if callable(raw_host):
                    raw_host = raw_host()
            except Exception as pgconn_err:
                raise DatabaseIdentityError(
                    f"Failed to inspect psycopg pgconn: {pgconn_err}"
                ) from pgconn_err
        else:
            raise DatabaseIdentityError(
                "Configured PostgreSQL driver 'psycopg' does not expose documented connection information API."
            )

        if raw_host is None:
            raise DatabaseIdentityError(
                "Database driver info missing required 'host' attribute."
            )
        if isinstance(raw_host, bytes):
            raw_host = raw_host.decode("utf-8", errors="replace")
        if not isinstance(raw_host, str):
            raise DatabaseIdentityError(
                "Database driver returned invalid non-string hostname."
            )
        host = raw_host.strip()
        if not host:
            raise DatabaseIdentityError(
                "Database driver returned empty hostname."
            )
        return host

    # Fail closed for any unhandled state
    raise DatabaseIdentityError(
        f"Unable to verify hostname for PostgreSQL driver '{driver_name}'."
    )


def verify_database_identity(
    conn: Connection | None = None,
    target_engine: Engine | None = None,
    app_settings: Settings | None = None,
) -> dict[str, str | None]:
    """Verify that the database connection matches the expected identity for staging isolation.

    In staging mode (or whenever EXPECTED_DATABASE_IDENTITY is configured), this function:
    1. Validates that EXPECTED_DATABASE_IDENTITY is non-empty.
    2. Validates that the engine URL is PostgreSQL and extracts its configured hostname.
    3. Compares the configured URL hostname against EXPECTED_DATABASE_IDENTITY (exact match).
    4. Connects (or reuses `conn`) and verifies driver-level hostname inspection (mandatory in staging).
    5. Fails closed if DBAPI connection is missing or driver inspection is unsupported.
    6. Compares the driver-reported hostname against EXPECTED_DATABASE_IDENTITY.
    7. Executes a read-only identity query (current_database, current_user, version).
    8. Sets driver_verified=True only after successful hostname verification.
    9. Fails closed and disposes of the connection/engine if any check fails.

    In non-staging environments (production, development, testing) where EXPECTED_DATABASE_IDENTITY
    is not set, verification passes through safely without modifying behavior.

    Note: Hostname matching verifies the network destination matches EXPECTED_DATABASE_IDENTITY,
    but does not independently prove Neon project identity. Project isolation relies on separate
    Neon projects, distinct credentials, and isolated environment configurations.
    """
    current_settings = app_settings or get_settings()
    current_engine = target_engine or (conn.engine if conn is not None else engine)

    # Pass-through if not in staging and no expected identity is set
    if current_settings.ENVIRONMENT != "staging" and not current_settings.EXPECTED_DATABASE_IDENTITY:
        return {"status": "skipped", "environment": current_settings.ENVIRONMENT}

    expected_identity = (
        current_settings.EXPECTED_DATABASE_IDENTITY.strip()
        if current_settings.EXPECTED_DATABASE_IDENTITY
        else ""
    )
    if not expected_identity:
        raise DatabaseIdentityError(
            "EXPECTED_DATABASE_IDENTITY is required when ENVIRONMENT is 'staging'."
        )

    # In staging, ensure a trusted root CA certificate bundle is available
    if current_settings.ENVIRONMENT == "staging":
        ca_bundle = resolve_ca_bundle_path()
        if not ca_bundle:
            raise DatabaseIdentityError(
                "Staging startup aborted: Unable to locate a valid root CA certificate bundle for strict TLS verification (sslmode=verify-full)."
            )

    # Inspect engine URL backend and hostname
    url = current_engine.url
    backend = url.get_backend_name()
    if backend not in ("postgresql", "postgres"):
        raise DatabaseIdentityError(
            f"Database isolation error: Staging must connect to PostgreSQL, found backend '{backend}'."
        )

    url_host = url.host
    if not url_host:
        raise DatabaseIdentityError(
            "Database isolation error: Cannot determine hostname from database connection URL."
        )

    if url_host.strip().lower() != expected_identity.lower():
        raise DatabaseIdentityMismatchError(
            f"Database identity mismatch: expected '{expected_identity}', but connection points to '{url_host}'."
        )

    def _execute_identity_checks(active_conn: Connection) -> dict[str, str | bool | None]:
        # Mandatory driver-level hostname verification
        driver_verified = False
        driver_host = _extract_driver_hostname(active_conn, current_engine)
        if driver_host.lower() != expected_identity.lower():
            raise DatabaseIdentityMismatchError(
                f"Database driver identity mismatch: connected to '{driver_host}', expected '{expected_identity}'."
            )
        driver_verified = True

        # Read-only query to verify connectivity and capture server session metadata
        try:
            result = active_conn.execute(
                text("SELECT current_database(), current_user, version();")
            ).fetchone()
        except Exception as e:
            raise DatabaseIdentityError(
                f"Failed to execute database identity query: {e}"
            ) from e

        if not result or not result[0] or not result[1]:
            raise DatabaseIdentityError(
                "Database identity verification failed: query returned empty or unusable session metadata."
            )

        db_name = str(result[0])
        db_user = str(result[1])
        db_version = str(result[2]) if len(result) > 2 and result[2] is not None else None

        logger.info(
            "Database identity verified: host=%s, db=%s, user=%s (driver_verified=%s)",
            expected_identity,
            db_name,
            db_user,
            driver_verified,
        )

        return {
            "status": "verified",
            "expected_identity": expected_identity,
            "connected_host": url_host,
            "current_database": db_name,
            "current_user": db_user,
            "version": db_version,
            "driver_verified": driver_verified,
        }

    # If caller provided an active connection, execute on it directly
    if conn is not None:
        return _execute_identity_checks(conn)

    # Standalone execution: manage local connection lifecycle
    managed_conn = None
    try:
        managed_conn = current_engine.connect()
        return _execute_identity_checks(managed_conn)
    except (DatabaseIdentityError, DatabaseIdentityMismatchError):
        if current_settings.ENVIRONMENT == "staging":
            current_engine.dispose()
        raise
    except Exception as e:
        if current_settings.ENVIRONMENT == "staging":
            current_engine.dispose()
        raise DatabaseIdentityError(
            f"Failed to connect and verify database identity: {e}"
        ) from e
    finally:
        if managed_conn:
            try:
                managed_conn.close()
            except Exception:
                pass


def init_db(
    target_engine: Engine | None = None,
    app_settings: Settings | None = None,
) -> None:
    """Create missing tables in the database after identity verification.

    SCHEMA EVOLUTION & MIGRATION LIMITATIONS:
    - Base.metadata.create_all() issues CREATE TABLE IF NOT EXISTS.
    - It creates MISSING tables only. It does NOT alter, add, or drop columns on existing tables.
    - init_db() and CLI --init are idempotent bootstrap tools, NOT a versioned migration engine.
    - Future non-additive or existing-table schema modifications require explicit versioned
      migration tooling (such as Alembic) applied prior to application deployment.
    - Milestone 5 (M5) release engineering performs NO production schema migrations.

    Guarantees that database identity verification and DDL creation use the EXACT SAME
    physical connection:
    1. Opens a physical connection via target_engine.begin().
    2. Runs verify_database_identity(conn=conn, ...) on that exact connection.
    3. If verification succeeds, executes Base.metadata.create_all(bind=conn) on that same connection.
    4. Handles legacy SQLite migrations on that same connection.
    5. If verification fails, the transaction rolls back, schema creation is completely bypassed,
       and the engine is disposed in staging.
    """
    current_settings = app_settings or get_settings()
    current_engine = target_engine or engine

    try:
        with current_engine.begin() as conn:
            # Step 1: Verify identity on the active connection
            verify_database_identity(
                conn=conn,
                target_engine=current_engine,
                app_settings=current_settings,
            )

            # Step 2: Create all tables on the EXACT SAME verified connection
            Base.metadata.create_all(bind=conn)

            # Step 3: SQLite legacy column migration on the same connection
            backend = current_engine.url.get_backend_name()
            if backend == "sqlite":
                try:
                    res = conn.execute(text("PRAGMA table_info(quiz_sets)"))
                    columns = [row[1] for row in res.fetchall()]
                    if "description" not in columns and len(columns) > 0:
                        conn.execute(text("ALTER TABLE quiz_sets ADD COLUMN description TEXT"))
                except Exception:
                    pass
    except (DatabaseIdentityError, DatabaseIdentityMismatchError):
        if current_settings.ENVIRONMENT == "staging":
            current_engine.dispose()
        raise
    except Exception as e:
        if current_settings.ENVIRONMENT == "staging":
            current_engine.dispose()
        raise


def get_db():
    """Context manager / generator for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def cli_main() -> None:
    """CLI entrypoint for database verification and schema management."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="QuizBotPro Database Operations & Verification Utility")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify database connectivity and host identity without running DDL",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Inspect connected database identity and list existing tables",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Bootstrap missing tables via Base.metadata.create_all() on verified connection. "
        "NOTE: create_all() creates missing tables only and does NOT alter existing tables. "
        "It is not a versioned migration engine. Future modifications to existing tables "
        "require explicit versioned migrations (e.g. Alembic). No migrations are run by M5.",
    )

    args = parser.parse_args()
    current_settings = get_settings()

    print("=" * 60)
    print(f"QuizBotPro Database Utility [Environment: {current_settings.ENVIRONMENT}]")
    print("=" * 60)

    try:
        with engine.begin() as conn:
            print("Step 1: Verifying database identity and TLS configuration...")
            verify_database_identity(
                conn=conn,
                target_engine=engine,
                app_settings=current_settings,
            )
            print(" [PASS] Database identity and TLS verification succeeded.")

            if args.verify_only:
                print("\nVerification complete. Database is healthy and verified.")
                sys.exit(0)

            from sqlalchemy import inspect
            inspector = inspect(conn)
            existing_tables = sorted(inspector.get_table_names())
            print(f"\nStep 2: Table inspection - {len(existing_tables)} existing tables:")
            for tbl in existing_tables:
                print(f"  - {tbl}")

            if args.status:
                print("\nStatus check complete.")
                sys.exit(0)

            if args.init:
                print("\nStep 3: Creating missing tables (Base.metadata.create_all)...")
                print("  Note: create_all() creates missing tables only; existing tables are not modified.")
                Base.metadata.create_all(bind=conn)
                print(" [PASS] Missing tables created successfully.")
                sys.exit(0)

        print("\nNo action specified. Use --verify-only, --status, or --init.")
    except DatabaseIdentityMismatchError as exc:
        print(f"\n[FAIL] CRITICAL: Database identity mismatch: {exc}")
        sys.exit(1)
    except DatabaseIdentityError as exc:
        print(f"\n[FAIL] Database identity/isolation failure: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[FAIL] Unexpected error connecting to database: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
