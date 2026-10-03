"""Configuration settings for BulkQuiz application."""

from functools import lru_cache
from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

# Canonical Telegram API poll limits
TELEGRAM_OPTION_MAX_LENGTH: int = 100
TELEGRAM_QUESTION_MAX_LENGTH: int = 300
TELEGRAM_EXPLANATION_MAX_LENGTH: int = 200


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = "mock_token_for_tests"

    # Database
    DATABASE_URL: str = "sqlite:///bulkquiz.db"

    # Logging & Environment
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    ENVIRONMENT: Literal["development", "production", "testing"] = "development"

    # Telegram Publishing & Rate Limiting defaults
    MAX_OPTIONS_PER_QUESTION: int = 10
    MIN_OPTIONS_PER_QUESTION: int = 2
    TELEGRAM_OPTION_MAX_LENGTH: int = TELEGRAM_OPTION_MAX_LENGTH
    DEFAULT_DELAY_BETWEEN_POSTS: float = 1.0  # seconds between quiz creations

    # AI Shortening Integration
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    AI_SHORTENER_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()

