"""Global pytest configuration and test environment isolation.

Enforces isolated in-memory test configuration before any application module is loaded.
Guarantees that test executions never connect to external database services or load
live credentials from .env.
"""

import os

# 1. Enforce safe testing environment variables
os.environ["ENVIRONMENT"] = "testing"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:mock_token_for_ci_testing_only"
os.environ.setdefault("LOG_LEVEL", "WARNING")

# 2. Reset LRU cache on settings to ensure tests observe the testing configuration
try:
    from app.config.settings import get_settings
    get_settings.cache_clear()
except Exception:
    pass
