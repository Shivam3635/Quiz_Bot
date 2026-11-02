"""Security and authorization tests for staging environment isolation."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import ValidationError
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.bot.middleware import (
    STAGING_ACCESS_DENIED_ALERT,
    STAGING_ACCESS_DENIED_TEXT,
    staging_security_gatekeeper,
)
from app.config.settings import Settings, get_settings
from app.main import create_bot_app


def make_mock_update(
    user_id: int | None = 12345,
    username: str | None = "testuser",
    is_callback: bool = False,
    is_message: bool = True,
    chat_type: str = ChatType.PRIVATE,
) -> MagicMock:
    """Helper to construct mocked Telegram Update objects without external network calls."""
    update = MagicMock(spec=Update)
    update.update_id = 99999

    if user_id is not None:
        user = MagicMock(spec=User)
        user.id = user_id
        user.username = username
        update.effective_user = user
    else:
        update.effective_user = None

    chat = MagicMock(spec=Chat)
    chat.id = -100123456
    chat.type = chat_type
    update.effective_chat = chat

    if is_callback:
        query = MagicMock(spec=CallbackQuery)
        query.answer = AsyncMock()
        query.data = "action_bulk_create"
        query.from_user = update.effective_user
        update.callback_query = query
        update.effective_message = None
    elif is_message:
        message = MagicMock(spec=Message)
        message.message_id = 1
        message.reply_text = AsyncMock()
        message.from_user = update.effective_user
        update.callback_query = None
        update.effective_message = message
        update.message = message
    else:
        update.callback_query = None
        update.effective_message = None
        update.message = None

    return update


# ============================================================================
# 1. Configuration & Setting Validation Tests
# ============================================================================

def test_comma_separated_telegram_ids_parse_correctly():
    """Verify numeric strings, whitespace, and JSON list parsing in STAGING_ALLOWED_USER_IDS."""
    s1 = Settings(STAGING_ALLOWED_USER_IDS="111, 222, 333")
    assert s1.STAGING_ALLOWED_USER_IDS == [111, 222, 333]

    s2 = Settings(STAGING_ALLOWED_USER_IDS=" 999999 ")
    assert s2.STAGING_ALLOWED_USER_IDS == [999999]

    s3 = Settings(STAGING_ALLOWED_USER_IDS="[101, 202, 303]")
    assert s3.STAGING_ALLOWED_USER_IDS == [101, 202, 303]

    s4 = Settings(STAGING_ALLOWED_USER_IDS=[444, 555])
    assert s4.STAGING_ALLOWED_USER_IDS == [444, 555]

    s5 = Settings(STAGING_ALLOWED_USER_IDS=777)
    assert s5.STAGING_ALLOWED_USER_IDS == [777]


def test_env_loading_comma_separated_ids(monkeypatch):
    """Verify comma-separated IDs loaded directly from actual environment variables."""
    monkeypatch.setenv("STAGING_ALLOWED_USER_IDS", "111, 222, 333")
    s = Settings()
    assert s.STAGING_ALLOWED_USER_IDS == [111, 222, 333]
    assert isinstance(s.STAGING_ALLOWED_USER_IDS, list)


def test_env_loading_json_array_ids(monkeypatch):
    """Verify JSON-array IDs loaded directly from actual environment variables."""
    monkeypatch.setenv("STAGING_ALLOWED_USER_IDS", "[444, 555, 666]")
    s = Settings()
    assert s.STAGING_ALLOWED_USER_IDS == [444, 555, 666]
    assert isinstance(s.STAGING_ALLOWED_USER_IDS, list)


def test_env_loading_invalid_input_rejected(monkeypatch):
    """Verify invalid non-numeric input loaded from environment variables is rejected."""
    monkeypatch.setenv("STAGING_ALLOWED_USER_IDS", "111, abc, 333")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "Invalid non-numeric Telegram ID" in str(exc.value)


def test_env_loading_empty_staging_allowlist_rejected(monkeypatch):
    """Verify empty allowlist in environment fails closed when ENVIRONMENT is staging."""
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:valid_token_for_staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system")
    monkeypatch.setenv("STAGING_ALLOWED_USER_IDS", "")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "STAGING_ALLOWED_USER_IDS must contain at least one valid Telegram user ID" in str(exc.value)


def test_empty_staging_allowlist_causes_validation_failure():
    """When ENVIRONMENT is staging, an empty or missing allowlist must fail closed immediately."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
            DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
            STAGING_ALLOWED_USER_IDS="",
        )
    assert "STAGING_ALLOWED_USER_IDS must contain at least one valid Telegram user ID" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info2:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
            DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
            STAGING_ALLOWED_USER_IDS=[],
        )
    assert "STAGING_ALLOWED_USER_IDS must contain at least one valid Telegram user ID" in str(exc_info2.value)


def test_invalid_telegram_ids_cause_validation_failure():
    """Ensure non-numeric, negative, zero, float, or empty elements trigger ValidationError."""
    # Negative ID
    with pytest.raises(ValidationError) as exc:
        Settings(STAGING_ALLOWED_USER_IDS="123, -456")
    assert "positive integer" in str(exc.value)

    # Zero ID
    with pytest.raises(ValidationError) as exc:
        Settings(STAGING_ALLOWED_USER_IDS="0")
    assert "positive integer" in str(exc.value)

    # Non-numeric text
    with pytest.raises(ValidationError) as exc:
        Settings(STAGING_ALLOWED_USER_IDS="123, abc, 456")
    assert "Invalid non-numeric Telegram ID" in str(exc.value)

    # Empty item (double comma)
    with pytest.raises(ValidationError) as exc:
        Settings(STAGING_ALLOWED_USER_IDS="123,,456")
    assert "Empty ID element" in str(exc.value)

    # Boolean value disguised as int
    with pytest.raises(ValidationError) as exc:
        Settings(STAGING_ALLOWED_USER_IDS=[True])
    assert "Invalid Telegram ID value" in str(exc.value)


def test_staging_rejects_mock_token_or_default_database():
    """Ensure staging fails closed if mock bot token or local default database is provided."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="mock_token_for_tests",
            DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb",
            STAGING_ALLOWED_USER_IDS="12345",
            EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
        )
    assert "Valid non-mock TELEGRAM_BOT_TOKEN is required" in str(exc.value)

    with pytest.raises(ValidationError) as exc2:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
            DATABASE_URL="sqlite:///quizbotpro.db",
            STAGING_ALLOWED_USER_IDS="12345",
            EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
        )
    assert "Dedicated non-default DATABASE_URL is required" in str(exc2.value)


def test_staging_rejects_whitespace_bot_token_or_database():
    """Ensure whitespace-only bot token or database URL cannot bypass staging validation."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="   ",
            DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb",
            STAGING_ALLOWED_USER_IDS="12345",
            EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
        )
    assert "Valid non-mock TELEGRAM_BOT_TOKEN is required" in str(exc.value)

    with pytest.raises(ValidationError) as exc2:
        Settings(
            ENVIRONMENT="staging",
            TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
            DATABASE_URL="   \t  ",
            STAGING_ALLOWED_USER_IDS="12345",
            EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
        )
    assert "Dedicated non-default DATABASE_URL is required" in str(exc2.value)


def test_expected_database_identity_config_field():
    """Verify EXPECTED_DATABASE_IDENTITY field is accepted for future strong identity checks."""
    s = Settings(EXPECTED_DATABASE_IDENTITY="neon-staging-branch-01")
    assert s.EXPECTED_DATABASE_IDENTITY == "neon-staging-branch-01"


# ============================================================================
# 2. Gatekeeper Authorization Tests
# ============================================================================

@pytest.mark.asyncio
async def test_staging_allows_authorized_telegram_user():
    """Staging must allow updates from authorized user IDs without raising ApplicationHandlerStop."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=111, username="authorized_admin")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        # Should complete normally without raising ApplicationHandlerStop
        await staging_security_gatekeeper(update, context)

    # Verify no rejection response was sent
    update.effective_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_staging_rejects_unauthorized_telegram_user():
    """Staging must reject unauthorized messages, send access-denied text, and stop propagation."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=999, username="intruder")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)

    update.effective_message.reply_text.assert_called_once()
    sent_text = update.effective_message.reply_text.call_args[0][0]
    assert "Access Restricted" in sent_text


@pytest.mark.asyncio
async def test_staging_silently_rejects_unauthorized_group_message():
    """Staging must silently reject unauthorized group messages without sending reply text."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=999, username="intruder", chat_type=ChatType.GROUP)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)

    # Must be silently rejected: no reply sent to the group
    update.effective_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_staging_silently_rejects_unauthorized_supergroup_message():
    """Staging must silently reject unauthorized supergroup messages without sending reply text."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=999, username="intruder", chat_type=ChatType.SUPERGROUP)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)

    # Must be silently rejected: no reply sent to the supergroup
    update.effective_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_staging_feedback_failure_never_bypasses_stop():
    """Even if sending rejection feedback raises an exception, ApplicationHandlerStop must be raised."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=999, chat_type=ChatType.PRIVATE)
    update.effective_message.reply_text.side_effect = RuntimeError("Telegram connection timeout")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)


@pytest.mark.asyncio
async def test_staging_rejects_unauthorized_callback_query():
    """Staging must reject unauthorized inline button clicks with an alert and stop propagation."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=999, is_callback=True)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)

    update.callback_query.answer.assert_called_once_with(
        STAGING_ACCESS_DENIED_ALERT,
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_staging_rejects_update_without_identifiable_user():
    """Updates lacking a verifiable effective_user must fail closed immediately."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=None, is_message=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)


@pytest.mark.asyncio
async def test_staging_unhandled_or_non_user_update_fails_closed():
    """Channel posts or system updates without effective_user must fail closed without errors."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = MagicMock(spec=Update)
    update.update_id = 10001
    update.effective_user = None
    update.effective_message = None
    update.callback_query = None
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)


# ============================================================================
# 3. Environment Compatibility Tests (Non-Staging Pass-Through)
# ============================================================================

@pytest.mark.asyncio
async def test_production_mode_passes_updates_through_unchanged():
    """In production mode, any user update must pass through gatekeeper without interruption."""
    mock_settings = Settings(ENVIRONMENT="production")
    update = make_mock_update(user_id=88888, username="public_user")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        # Should return without raising ApplicationHandlerStop
        await staging_security_gatekeeper(update, context)

    update.effective_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_development_mode_passes_updates_through_unchanged():
    """In development mode, gatekeeper must pass updates through without restrictions."""
    mock_settings = Settings(ENVIRONMENT="development")
    update = make_mock_update(user_id=77777, username="dev_tester")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        await staging_security_gatekeeper(update, context)

    update.effective_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_testing_mode_remains_compatible():
    """In testing mode, gatekeeper must pass updates through without restrictions."""
    mock_settings = Settings(ENVIRONMENT="testing")
    update = make_mock_update(user_id=55555, username="unit_test_user")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        await staging_security_gatekeeper(update, context)

    update.effective_message.reply_text.assert_not_called()


def test_app_factory_wires_gatekeeper_in_group_minus_one():
    """Verify create_bot_app registers the gatekeeper TypeHandler in priority group -1 with mocked init_db."""
    from telegram.ext import TypeHandler

    with patch("app.main.init_db") as mock_init:
        app = create_bot_app()
        mock_init.assert_called_once()
        assert -1 in app.handlers
        group_minus_one_handlers = app.handlers[-1]
        assert len(group_minus_one_handlers) >= 1
        handler = group_minus_one_handlers[0]
        assert isinstance(handler, TypeHandler)
        assert handler.callback is staging_security_gatekeeper


@pytest.mark.asyncio
async def test_unauthorized_update_cannot_reach_downstream_handler():
    """Demonstrate that an unauthorized update processed through gatekeeper raises ApplicationHandlerStop and halts."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=999, username="intruder")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    downstream_handler = AsyncMock()

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)
            await downstream_handler(update, context)

    downstream_handler.assert_not_called()


@pytest.mark.asyncio
async def test_staging_callback_feedback_failure_never_bypasses_stop():
    """Even if answering callback query raises an exception, ApplicationHandlerStop must still be raised."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111, 222",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    update = make_mock_update(user_id=999, is_callback=True)
    update.callback_query.answer.side_effect = RuntimeError("Network error answering callback")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("app.bot.middleware.get_settings", return_value=mock_settings):
        with pytest.raises(ApplicationHandlerStop):
            await staging_security_gatekeeper(update, context)


def test_staging_aborts_startup_on_generic_database_init_failure():
    """In staging, any database initialization failure must raise and abort create_bot_app()."""
    mock_settings = Settings(
        ENVIRONMENT="staging",
        TELEGRAM_BOT_TOKEN="123456:valid_token_for_staging",
        DATABASE_URL="postgresql://user:pass@ep-staging.region.neon.tech/neondb?sslmode=verify-full&sslrootcert=system",
        STAGING_ALLOWED_USER_IDS="111",
        EXPECTED_DATABASE_IDENTITY="ep-staging.region.neon.tech",
    )
    with patch("app.main.get_settings", return_value=mock_settings), \
         patch("app.main.init_db", side_effect=RuntimeError("Table creation DDL error")):
        with pytest.raises(RuntimeError) as exc:
            create_bot_app()
        assert "Staging startup aborted: database initialization failed" in str(exc.value)


def test_production_logs_warning_and_continues_on_database_init_failure():
    """In production, database init failure logs a warning and allows bot creation to proceed (preserving existing behavior)."""
    prod_settings = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        TELEGRAM_BOT_TOKEN="123456:prod_token",
        DATABASE_URL="postgresql://user:pass@ep-prod-999.neon.tech/neondb",
    )
    with patch("app.main.get_settings", return_value=prod_settings), \
         patch("app.main.init_db", side_effect=RuntimeError("Transient DB connection warning")):
        app = create_bot_app()
        assert app is not None

