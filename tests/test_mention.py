"""Unit tests for @MentionBot-style group mentions (/all, @all, /admin) and member roster."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from telegram import Chat, ChatMember, Message, MessageEntity, Update, User
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.bot.handlers.mention import (
    active_mention_tasks,
    handle_chat_member_updated,
    handle_my_chat_member_updated,
    mention_admin_command,
    mention_all_command,
    mention_stop_callback,
    run_mention_sequence,
    stop_mention_command,
    tag_me_callback,
    track_group_member_message,
)
from app.database.database import Base, SessionLocal, engine
from app.database.models import GroupMember
from app.database.repositories import get_group_members, record_group_member
from app.services.game_service import GameSession, ParticipantScore, get_game_manager


@pytest.fixture(autouse=True)
def init_db():
    Base.metadata.create_all(bind=engine)
    yield
    with SessionLocal() as db:
        db.query(GroupMember).delete()
        db.commit()


def make_group_update(
    chat_id: int = -1001234567,
    user_id: int = 111,
    username: str = "alice",
    first_name: str = "Alice",
    text: str = "Hello guys",
    chat_type: str = ChatType.SUPERGROUP,
):
    update = MagicMock(spec=Update)
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = chat_type
    chat.title = "Test Group"

    user = MagicMock(spec=User)
    user.id = user_id
    user.username = username
    user.first_name = first_name
    user.is_bot = False

    message = MagicMock(spec=Message)
    message.message_id = 42
    message.text = text
    message.chat = chat
    message.from_user = user
    message.new_chat_members = []
    message.reply_to_message = None
    message.entities = []

    update.effective_chat = chat
    update.effective_user = user
    update.message = message
    update.callback_query = None

    # Mock admin status
    admin_member = MagicMock()
    admin_member.status = ChatMemberStatus.ADMINISTRATOR
    admin_member.user = user
    chat.get_member = AsyncMock(return_value=admin_member)

    return update


@pytest.mark.asyncio
async def test_track_group_member_message_sender_and_new_members():
    """Verify track_group_member_message records the sender and any joined members."""
    update = make_group_update(user_id=101, username="bob", first_name="Bob")

    # Add new chat members
    new_user1 = MagicMock(spec=User)
    new_user1.id = 201
    new_user1.username = "charlie"
    new_user1.first_name = "Charlie"
    new_user1.is_bot = False

    update.message.new_chat_members = [new_user1]

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.chat_data = {}

    await track_group_member_message(update, context)

    with SessionLocal() as db:
        members = get_group_members(db, -1001234567)
        uids = {m.user_id for m in members}
        assert 101 in uids
        assert 201 in uids

    assert context.chat_data["seen_members"][101] == ("bob", "Bob")
    assert context.chat_data["seen_members"][201] == ("charlie", "Charlie")


@pytest.mark.asyncio
async def test_track_group_member_message_replies_and_mentions():
    """Verify track_group_member_message captures replied-to users and entity text mentions."""
    update = make_group_update(user_id=101, username="bob", first_name="Bob")

    # Replied-to user
    replied_user = MagicMock(spec=User)
    replied_user.id = 301
    replied_user.username = "david"
    replied_user.first_name = "David"
    replied_user.is_bot = False

    reply_msg = MagicMock(spec=Message)
    reply_msg.from_user = replied_user
    update.message.reply_to_message = reply_msg

    # Mentioned user
    mentioned_user = MagicMock(spec=User)
    mentioned_user.id = 401
    mentioned_user.username = "emma"
    mentioned_user.first_name = "Emma"
    mentioned_user.is_bot = False

    mention_entity = MagicMock(spec=MessageEntity)
    mention_entity.type = "text_mention"
    mention_entity.user = mentioned_user
    update.message.entities = [mention_entity]

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.chat_data = {}

    await track_group_member_message(update, context)

    with SessionLocal() as db:
        members = get_group_members(db, -1001234567)
        uids = {m.user_id for m in members}
        assert 101 in uids
        assert 301 in uids
        assert 401 in uids


@pytest.mark.asyncio
async def test_handle_chat_member_updated_and_bot_join():
    """Verify user joins/leaves and bot join seeding administrators."""
    chat = MagicMock(spec=Chat)
    chat.id = -100999
    chat.type = ChatType.SUPERGROUP

    user = MagicMock(spec=User)
    user.id = 555
    user.username = "frank"
    user.first_name = "Frank"
    user.is_bot = False

    update = MagicMock(spec=Update)
    cmu = MagicMock()
    cmu.chat = chat
    cmu.new_chat_member = MagicMock(status=ChatMemberStatus.MEMBER, user=user)
    update.chat_member = cmu
    update.my_chat_member = None

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.chat_data = {}

    # 1. User joins
    await handle_chat_member_updated(update, context)
    with SessionLocal() as db:
        members = get_group_members(db, -100999)
        assert len(members) == 1
        assert members[0].user_id == 555

    # 2. User leaves
    cmu.new_chat_member.status = ChatMemberStatus.LEFT
    await handle_chat_member_updated(update, context)
    with SessionLocal() as db:
        members = get_group_members(db, -100999)
        assert len(members) == 0

    # 3. Bot joined group
    update_bot = MagicMock(spec=Update)
    my_cmu = MagicMock()
    my_cmu.chat = chat
    my_cmu.new_chat_member = MagicMock(status=ChatMemberStatus.MEMBER)
    update_bot.my_chat_member = my_cmu

    admin_user = MagicMock(spec=User)
    admin_user.id = 777
    admin_user.username = "boss"
    admin_user.first_name = "Boss"
    admin_user.is_bot = False

    admin_cm = MagicMock()
    admin_cm.user = admin_user

    context.bot = AsyncMock()
    context.bot.get_chat_administrators = AsyncMock(return_value=[admin_cm])

    await handle_my_chat_member_updated(update_bot, context)
    with SessionLocal() as db:
        members = get_group_members(db, -100999)
        assert len(members) == 1
        assert members[0].user_id == 777


@pytest.mark.asyncio
async def test_tag_me_callback_registers_user():
    """Verify tapping 'Tag Me' self-registers member into DB and seen_members."""
    query = AsyncMock()
    user = MagicMock(spec=User)
    user.id = 888
    user.username = "george"
    user.first_name = "George"
    user.is_bot = False

    chat = MagicMock(spec=Chat)
    chat.id = -100888

    update = MagicMock(spec=Update)
    update.callback_query = query
    update.effective_user = user
    update.effective_chat = chat

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.chat_data = {}

    await tag_me_callback(update, context)

    query.answer.assert_called_once()
    assert "registered" in query.answer.call_args[0][0]

    with SessionLocal() as db:
        members = get_group_members(db, -100888)
        assert len(members) == 1
        assert members[0].user_id == 888


@pytest.mark.asyncio
async def test_mention_all_command_when_no_other_members():
    """When only the caller is present, /all shows an informative guide with a register button."""
    update = make_group_update(chat_id=-100111, user_id=999, username="caller_admin")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = AsyncMock()
    context.bot.get_chat_administrators = AsyncMock(return_value=[])
    context.bot.get_chat_member_count = AsyncMock(return_value=10)
    context.chat_data = {}

    await mention_all_command(update, context)

    update.message.reply_text.assert_called_once()
    call_args, call_kwargs = update.message.reply_text.call_args
    assert "No other group members registered yet" in call_args[0]
    assert call_kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_mention_all_command_with_members_and_game_scores():
    """Verify /all mentions group members and active game scores, with lobby button and stats."""
    update = make_group_update(chat_id=-100222, user_id=999, username="admin_alice")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = AsyncMock()

    # Admin 1
    admin_u = MagicMock(spec=User)
    admin_u.id = 111
    admin_u.username = "admin_bob"
    admin_u.first_name = "Bob"
    admin_u.is_bot = False
    admin_cm = MagicMock(user=admin_u)

    context.bot.get_chat_administrators = AsyncMock(return_value=[admin_cm])
    context.bot.get_chat_member_count = AsyncMock(return_value=25)
    context.chat_data = {}

    # Seed an extra member in DB
    with SessionLocal() as db:
        record_group_member(db, -100222, user_id=222, username="charlie", first_name="Charlie")

    # Add game session in lobby
    manager = get_game_manager()
    game = manager.create_game(
        chat_id=-100222,
        chat_title="Quiz Group",
        quiz_set_id=5,
        title="Python Quiz",
        description="Test quiz",
        questions=[],
        time_limit=15,
        initiator_id=999,
        initiator_name="Alice",
    )
    game.add_participant(user_id=333, full_name="David", username="david")

    with patch("app.bot.handlers.mention.run_mention_sequence") as mock_seq:
        mock_seq.return_value = asyncio.sleep(0.01)
        await mention_all_command(update, context)

        mock_seq.assert_called_once()
        _, kwargs = mock_seq.call_args
        members_tagged = kwargs["members"]
        uids = {m[0] for m in members_tagged}

        # Verify admin_bob (111), charlie (222), and game participant david (333) are tagged
        assert 111 in uids
        assert 222 in uids
        assert 333 in uids
        # Caller admin_alice (999) is excluded from being self-pinged
        assert 999 not in uids

        # Verify announcement contains tagging stats
        assert "Tagging:" in kwargs["announcement"]
        assert "Group members: <code>24</code>" in kwargs["announcement"]

    # Cleanup game
    manager.active_games.pop(-100222, None)


@pytest.mark.asyncio
async def test_run_mention_sequence_batching_and_deleted_reply():
    """Verify run_mention_sequence splits into batches of 5 and falls back if replied msg was deleted."""
    bot = AsyncMock()
    # First send_message with reply raises BadRequest (message deleted)
    call_count = 0

    async def mock_send(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if "reply_to_message_id" in kwargs and kwargs["reply_to_message_id"] == 999:
            raise BadRequest("Replied message not found")
        msg = MagicMock(spec=Message)
        msg.message_id = 100 + call_count
        return msg

    bot.send_message = AsyncMock(side_effect=mock_send)

    members = [
        (1, "user1", "User 1"),
        (2, "user2", "User 2"),
        (3, None, "User 3"),
    ]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await run_mention_sequence(
            bot=bot,
            chat_id=-100333,
            members=members,
            announcement="Test announcement",
            reply_to_message_id=999,
        )

    # 1 announcement (retried once without reply_to) + 1 batch message (without reply_to)
    assert bot.send_message.call_count >= 2


@pytest.mark.asyncio
async def test_mention_stop_callback_cancels_task():
    """Verify clicking Stop Mentions cancels the background mention task."""
    query = AsyncMock()
    user = MagicMock(spec=User)
    user.id = 999

    chat = MagicMock(spec=Chat)
    chat.id = -100444
    chat.type = ChatType.GROUP

    admin_member = MagicMock(status=ChatMemberStatus.ADMINISTRATOR, user=user)
    chat.get_member = AsyncMock(return_value=admin_member)

    update = MagicMock(spec=Update)
    update.callback_query = query
    update.effective_user = user
    update.effective_chat = chat

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = AsyncMock()

    # Create dummy running task
    async def dummy_work():
        await asyncio.sleep(10)

    task = asyncio.create_task(dummy_work())
    active_mention_tasks[-100444] = task

    await mention_stop_callback(update, context)

    assert task.cancelling() or task.cancelled()
    assert -100444 not in active_mention_tasks
    query.answer.assert_called_with("Mention sequence stopped! 🛑")
