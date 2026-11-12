"""Configuration settings for QuizBotPro application."""

import json
from functools import lru_cache
from typing import Annotated, Any, Literal
from typing_extensions import Self
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine.url import make_url


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = "mock_token_for_tests"

    # Database
    DATABASE_URL: str = "sqlite:///quizbotpro.db"

    # Optional explicit database identity verification (for staging isolation phase)
    EXPECTED_DATABASE_IDENTITY: str | None = None

    # Logging & Environment
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    ENVIRONMENT: Literal["development", "staging", "production", "testing"] = "development"

    # Staging Security: list of numeric Telegram User IDs allowed in staging
    STAGING_ALLOWED_USER_IDS: Annotated[list[int], NoDecode] = []

    # Telegram Publishing & Rate Limiting defaults
    MAX_OPTIONS_PER_QUESTION: int = 10
    MIN_OPTIONS_PER_QUESTION: int = 2
    DEFAULT_DELAY_BETWEEN_POSTS: float = 2.5  # seconds between quiz creations (aligned with Telegram's 20 msgs/min per-channel limit)
    DEFAULT_MESSAGE_PACING_INTERVAL: float = 3.0  # seconds between successive outgoing messages to the same destination chat
    MAX_FLOOD_RETRIES: int = 3  # maximum RetryAfter flood control retry attempts per question
    HEADER_BANNER_DELAY: float = 2.0  # seconds to pause after sending introductory header banner
    EXTENDED_OPTION_SUB_DELAY: float = 1.0  # seconds to pause between text message and letter poll in Solution 1

    # AI & Multimodal OCR (Phase 5)
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.8-flash"
    GEMINI_TIMEOUT_SECONDS: float = 30.0
    MAX_OCR_PAGES: int = 10
    MAX_CONCURRENT_EXTRACTIONS: int = 5

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("STAGING_ALLOWED_USER_IDS", mode="before")
    @classmethod
    def parse_staging_allowed_user_ids(cls, v: Any) -> list[int]:
        """Parse comma-separated strings, JSON lists, or integer iterables into positive Telegram user IDs."""
        if v is None or v == "":
            return []

        items: list[Any]
        if isinstance(v, str):
            v_strip = v.strip()
            if not v_strip:
                return []
            if v_strip.startswith("[") and v_strip.endswith("]"):
                try:
                    items = json.loads(v_strip)
                    if not isinstance(items, list):
                        raise ValueError("JSON in STAGING_ALLOWED_USER_IDS must be a list")
                except Exception as e:
                    raise ValueError(f"Invalid JSON format in STAGING_ALLOWED_USER_IDS: {e}") from e
            else:
                items = v_strip.split(",")
        elif isinstance(v, (list, tuple, set)):
            items = list(v)
        elif isinstance(v, int) and not isinstance(v, bool):
            items = [v]
        else:
            raise ValueError(f"Invalid STAGING_ALLOWED_USER_IDS format: {type(v).__name__}")

        parsed_ids: list[int] = []
        for raw_item in items:
            if isinstance(raw_item, str):
                item_str = raw_item.strip()
                if not item_str:
                    raise ValueError("Empty ID element found in STAGING_ALLOWED_USER_IDS")
                try:
                    val = int(item_str)
                except ValueError as err:
                    raise ValueError(f"Invalid non-numeric Telegram ID: {item_str!r}") from err
            elif isinstance(raw_item, int) and not isinstance(raw_item, bool):
                val = raw_item
            else:
                raise ValueError(f"Invalid Telegram ID value: {raw_item!r}")

            if val <= 0:
                raise ValueError(f"Telegram user ID must be a positive integer, got: {val}")

            if val not in parsed_ids:
                parsed_ids.append(val)

        return parsed_ids

    @model_validator(mode="after")
    def validate_staging_security(self) -> Self:
        """Enforce strict staging security constraints when ENVIRONMENT is 'staging'."""
        if self.ENVIRONMENT == "staging":
            if not self.STAGING_ALLOWED_USER_IDS:
                raise ValueError(
                    "STAGING_ALLOWED_USER_IDS must contain at least one valid Telegram user ID when ENVIRONMENT is 'staging'."
                )
            token_clean = self.TELEGRAM_BOT_TOKEN.strip() if self.TELEGRAM_BOT_TOKEN else ""
            if not token_clean or token_clean in (
                "your_bot_token_here",
                "mock_token_for_tests",
            ):
                raise ValueError(
                    "Valid non-mock TELEGRAM_BOT_TOKEN is required when ENVIRONMENT is 'staging'."
                )

            # Staging Database Identity & URL Verification
            expected_identity = (
                self.EXPECTED_DATABASE_IDENTITY.strip()
                if self.EXPECTED_DATABASE_IDENTITY
                else ""
            )
            if not expected_identity:
                raise ValueError(
                    "EXPECTED_DATABASE_IDENTITY is required when ENVIRONMENT is 'staging'."
                )

            db_url_clean = self.DATABASE_URL.strip() if self.DATABASE_URL else ""
            if not db_url_clean or db_url_clean in (
                "sqlite:///quizbotpro.db",
            ):
                raise ValueError(
                    "Dedicated non-default DATABASE_URL is required when ENVIRONMENT is 'staging'."
                )

            try:
                parsed_url = make_url(db_url_clean)
            except Exception as e:
                raise ValueError(
                    f"Invalid or malformed DATABASE_URL: unable to parse connection URL."
                ) from e

            backend = parsed_url.get_backend_name()
            if backend not in ("postgresql", "postgres"):
                raise ValueError(
                    f"Staging database must use PostgreSQL. Found unsupported scheme: '{backend}'."
                )

            url_host = parsed_url.host
            if not url_host:
                raise ValueError(
                    "Invalid or malformed DATABASE_URL: missing hostname in database URL."
                )

            if url_host.strip().lower() != expected_identity.lower():
                raise ValueError(
                    f"Database identity mismatch: expected '{expected_identity}', but DATABASE_URL points to '{url_host}'."
                )

            # Staging TLS & CA trust validation: strictly require sslmode=verify-full and valid sslrootcert
            query_params = parsed_url.query or {}

            # 1. Require sslmode parameter
            if "sslmode" not in query_params:
                raise ValueError(
                    "Missing required 'sslmode' parameter in DATABASE_URL. "
                    "Staging strictly requires 'sslmode=verify-full'."
                )

            raw_sslmode = query_params["sslmode"]

            # Guard against duplicate or conflicting parameter definitions (SQLAlchemy parses duplicates as tuple)
            if isinstance(raw_sslmode, (tuple, list)):
                raise ValueError(
                    f"Conflicting or duplicate 'sslmode' parameters in DATABASE_URL: {raw_sslmode!r}. "
                    "Staging requires exactly one 'sslmode=verify-full' parameter."
                )

            if not isinstance(raw_sslmode, str) or not raw_sslmode.strip():
                raise ValueError(
                    "Empty or malformed 'sslmode' parameter in DATABASE_URL. "
                    "Staging strictly requires 'sslmode=verify-full'."
                )

            sslmode = raw_sslmode.strip().lower()
            if sslmode != "verify-full":
                raise ValueError(
                    f"Invalid or insecure sslmode '{raw_sslmode}' is not permitted for staging PostgreSQL connections. "
                    "Staging strictly requires 'sslmode=verify-full'. "
                    "All other modes (including 'require', 'verify-ca', 'prefer', 'allow', and 'disable') are rejected."
                )

            # 2. Require non-empty sslrootcert parameter
            if "sslrootcert" not in query_params:
                raise ValueError(
                    "Missing required 'sslrootcert' parameter in DATABASE_URL. "
                    "When using sslmode='verify-full', 'sslrootcert=system' (or an explicit CA cert path) "
                    "must be configured to prevent libpq from failing on missing '~/.postgresql/root.crt'."
                )

            raw_sslrootcert = query_params["sslrootcert"]

            # Guard against duplicate or conflicting sslrootcert definitions
            if isinstance(raw_sslrootcert, (tuple, list)):
                raise ValueError(
                    f"Conflicting or duplicate 'sslrootcert' parameters in DATABASE_URL: {raw_sslrootcert!r}. "
                    "Staging requires exactly one 'sslrootcert' parameter."
                )

            if not isinstance(raw_sslrootcert, str) or not raw_sslrootcert.strip():
                raise ValueError(
                    "Empty or malformed 'sslrootcert' parameter in DATABASE_URL. "
                    "Staging strictly requires 'sslrootcert=system' or a valid non-empty CA certificate path."
                )
        return self


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
