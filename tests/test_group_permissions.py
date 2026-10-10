"""Tests for group admin permissions and command restriction."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram import Chat, Message, Update, User
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from app.bot.handlers.common import (
    ADMIN_REQUIRED_MESSAGE,
    check_group_admin_permission,
    is_user_group_admin,
    require_group_admin,
)
from app.bot.handlers.start import start_command
from app.bot.handlers.help import help_command, guide_command, template_command
from app.bot.handlers.game import startquiz_command, stopquiz_command


def make_update(
    chat_type: str = ChatType.GROUP,
    user_id: int = 12345,
    user_status: str = "member",
    is_callback: bool = False,
    is_anonymous_admin: bool = False,
):
    """Helper to build a mocked Update object with Chat and User."""
    update = MagicMock(spec=Update)
    chat = MagicMock(spec=Chat)
    chat.id = -100123456789
    chat.type = chat_type
    chat.title = "Test Group"

    user = MagicMock(spec=User)
    user.id = 1087968824 if is_anonymous_admin else user_id
    user.first_name = "RegularUser"
    user.username = "GroupAnonymousBot" if is_anonymous_admin else "regular_user"

    update.effective_chat = chat
    update.effective_user = user

    # Mock chat.get_member
    member = MagicMock()
    member.status = user_status
    member.user = user
    chat.get_member = AsyncMock(return_value=member)

    if is_callback:
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.data = "test_action"
        update.callback_query = query
        update.message = None
    else:
        message = MagicMock(spec=Message)
        message.reply_text = AsyncMock()
        update.message = message
        update.callback_query = None

    return update


@pytest.mark.asyncio
async def test_is_user_group_admin_private_chat():
    """Private chats should always allow users."""
    bot = AsyncMock()
    chat = MagicMock(spec=Chat)
    chat.type = ChatType.PRIVATE
    assert await is_user_group_admin(bot, chat, user_id=999) is True


@pytest.mark.asyncio
async def test_is_user_group_admin_in_group():
    """Group creator/administrator returns True, regular member returns False."""
    bot = AsyncMock()
    chat = MagicMock(spec=Chat)
    chat.type = ChatType.GROUP

    # Regular member
    m_member = MagicMock()
    m_member.status = "member"
    m_member.user = MagicMock(username="normal")
    chat.get_member = AsyncMock(return_value=m_member)
    assert await is_user_group_admin(bot, chat, user_id=111) is False

    # Administrator
    m_admin = MagicMock()
    m_admin.status = "administrator"
    m_admin.user = MagicMock(username="admin_user")
    chat.get_member = AsyncMock(return_value=m_admin)
    assert await is_user_group_admin(bot, chat, user_id=222) is True

    # Creator / Owner
    m_creator = MagicMock()
    m_creator.status = "creator"
    m_creator.user = MagicMock(username="creator_user")
    chat.get_member = AsyncMock(return_value=m_creator)
    assert await is_user_group_admin(bot, chat, user_id=333) is True

    # Anonymous group admin (ID 1087968824)
    assert await is_user_group_admin(bot, chat, user_id=1087968824) is True


@pytest.mark.asyncio
async def test_regular_member_blocked_on_start_command_in_group():
    """Normal member clicking /start in group should be blocked with admin message."""
    update = make_update(chat_type=ChatType.GROUP, user_status="member")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = []
    context.bot = AsyncMock()

    await start_command(update, context)

    # Verify admin message was sent
    update.message.reply_text.assert_called_once()
    sent_text = update.message.reply_text.call_args[0][0]
    assert "You should be an admin to run this command!" in sent_text


@pytest.mark.asyncio
async def test_admin_allowed_on_start_command_in_group():
    """Group admin running /start in group receives group mode guide instead of being blocked."""
    update = make_update(chat_type=ChatType.GROUP, user_status="administrator")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = []
    context.bot = AsyncMock()
    context.bot.username = "my_bulk_quiz_bot"

    await start_command(update, context)

    update.message.reply_text.assert_called_once()
    sent_text = update.message.reply_text.call_args[0][0]
    assert "QuizBotPro Group Mode" in sent_text
    assert "You should be an admin" not in sent_text


@pytest.mark.asyncio
async def test_regular_member_blocked_on_help_guide_template():
    """Normal member running /help, /guide, /template in group should be blocked."""
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = AsyncMock()

    for cmd_handler in (help_command, guide_command, template_command):
        update = make_update(chat_type=ChatType.SUPERGROUP, user_status="member")
        await cmd_handler(update, context)
        update.message.reply_text.assert_called_once()
        sent_text = update.message.reply_text.call_args[0][0]
        assert "You should be an admin to run this command!" in sent_text


@pytest.mark.asyncio
async def test_regular_member_blocked_on_startquiz_and_stopquiz():
    """Normal member running /startquiz or /stopquiz in group should be blocked."""
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = AsyncMock()
    context.args = ["10"]

    for cmd_handler in (startquiz_command, stopquiz_command):
        update = make_update(chat_type=ChatType.SUPERGROUP, user_status="member")
        await cmd_handler(update, context)
        update.message.reply_text.assert_called_once()
        sent_text = update.message.reply_text.call_args[0][0]
        assert "You should be an admin to run this command!" in sent_text


@pytest.mark.asyncio
async def test_private_chat_not_blocked():
    """In private chat (DM), any user can freely run /start and /help."""
    update = make_update(chat_type=ChatType.PRIVATE, user_status="member")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = []
    context.bot = AsyncMock()

    await help_command(update, context)

    update.message.reply_text.assert_called_once()
    sent_text = update.message.reply_text.call_args[0][0]
    assert "Quick Help & Guidelines" in sent_text
    assert "You should be an admin" not in sent_text
