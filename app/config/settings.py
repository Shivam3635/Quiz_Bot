"""Configuration settings for QuizBotPro application."""

from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = "mock_token_for_tests"

    # Database
    DATABASE_URL: str = "sqlite:///quizbotpro.db"

    # Logging & Environment
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    ENVIRONMENT: Literal["development", "production", "testing"] = "development"

    # Telegram Publishing & Rate Limiting defaults
    MAX_OPTIONS_PER_QUESTION: int = 10
    MIN_OPTIONS_PER_QUESTION: int = 2
    DEFAULT_DELAY_BETWEEN_POSTS: float = 1.0  # seconds between quiz creations

    # AI & Multimodal OCR (Phase 5)
    GEMINI_API_KEY: str | None = None

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
