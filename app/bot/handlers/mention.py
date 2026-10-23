"""Handlers for @MentionBot-style mass group mentions (/all, @all, /admin)."""

import asyncio
import html
from typing import Optional

from telegram import ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.ext import ContextTypes

from app.bot.handlers.common import is_user_group_admin, require_group_admin
from app.database.database import SessionLocal
from app.database.repositories import (
    deactivate_group_member,
    get_group_members,
    record_group_member,
)
from app.services.game_service import get_game_manager
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Tracks active background mention tasks by chat_id so they can be cancelled
active_mention_tasks: dict[int, asyncio.Task] = {}


async def track_group_member_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently record active group members into the database for mass mentions."""
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user or user.is_bot:
        return

    chat_type = getattr(chat, "type", None)
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"):
        return

    try:
        with SessionLocal() as db:
            record_group_member(
                db=db,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )
    except Exception as e:
        logger.debug("Failed to record group member %s in chat %s: %s", user.id, chat.id, e)


async def handle_chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track when users join or leave the group."""
    chat_member = update.chat_member
    if not chat_member:
        return

    chat = chat_member.chat
    user = chat_member.new_chat_member.user
    if not chat or not user or user.is_bot:
        return

    new_status = chat_member.new_chat_member.status
    try:
        with SessionLocal() as db:
            if new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                record_group_member(
                    db=db,
                    chat_id=chat.id,
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                )
            elif new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                deactivate_group_member(db=db, chat_id=chat.id, user_id=user.id)
    except Exception as e:
        logger.debug("Failed to handle chat member update for %s: %s", user.id, e)


async def run_mention_sequence(
    bot,
    chat_id: int,
    members: list[tuple[int, Optional[str], Optional[str]]],
    announcement: str,
    reply_to_message_id: Optional[int] = None,
    lobby_button: Optional[InlineKeyboardMarkup] = None,
) -> None:
    """Execute gradual @MentionBot-style batch mention sequence with 1.5s delay."""
    try:
        # Step 1: Send initial announcement
        initial_text = f"📢 <b>Announcement:</b>\n\n{announcement}"
        await bot.send_message(
            chat_id=chat_id,
            text=initial_text,
            reply_markup=lobby_button,
            reply_to_message_id=reply_to_message_id,
            parse_mode=ParseMode.HTML,
        )

        # Step 2: Chunk members into batches of 5
        batch_size = 5
        batches = [members[i : i + batch_size] for i in range(0, len(members), batch_size)]

        for idx, batch in enumerate(batches, start=1):
            await asyncio.sleep(1.5)

            # Format each member as @username or clickable tg://user link
            tags = []
            for uid, uname, fname in batch:
                if uname:
                    tags.append(f"@{uname}")
                else:
                    safe_name = html.escape(fname or "Player")
                    tags.append(f'<a href="tg://user?id={uid}">{safe_name}</a>')

            batch_text = " ".join(tags)
            await bot.send_message(
                chat_id=chat_id,
                text=batch_text,
                reply_to_message_id=reply_to_message_id,
                parse_mode=ParseMode.HTML,
            )

    except asyncio.CancelledError:
        logger.info("Mention sequence cancelled for chat %s", chat_id)
        await bot.send_message(
            chat_id=chat_id,
            text="🛑 <b>Mention sequence stopped.</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("Error running mention sequence in chat %s: %s", chat_id, e)
    finally:
        active_mention_tasks.pop(chat_id, None)


@require_group_admin
async def mention_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /all, /tagall, @all, #all to mass-mention group members like @MentionBot."""
    chat = update.effective_chat
    user = update.effective_user
    msg = update.message
    if not chat or not msg:
        return

    chat_type = getattr(chat, "type", None)
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"):
        await msg.reply_text(
            "ℹ️ The <code>/all</code> command can only be used inside groups!",
            parse_mode=ParseMode.HTML,
        )
        return

    # Cancel any currently active mention task in this chat
    if chat.id in active_mention_tasks:
        existing_task = active_mention_tasks[chat.id]
        if not existing_task.done():
            existing_task.cancel()
            active_mention_tasks.pop(chat.id, None)

    # Extract announcement text (either from command args or text trigger)
    raw_text = msg.text or ""
    parts = raw_text.split(maxsplit=1)
    custom_text = parts[1].strip() if len(parts) > 1 else ""

    # Check if this command was sent as a reply to another message
    reply_to_id = msg.reply_to_message.message_id if msg.reply_to_message else None

    # Fetch administrators
    members_dict: dict[int, tuple[Optional[str], Optional[str]]] = {}
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        for admin in admins:
            if admin.user and not admin.user.is_bot:
                members_dict[admin.user.id] = (admin.user.username, admin.user.first_name)
    except Exception as e:
        logger.warning("Could not fetch administrators for chat %s: %s", chat.id, e)

    # Fetch saved group members from DB
    try:
        with SessionLocal() as db:
            db_members = get_group_members(db, chat.id)
            for m in db_members:
                if m.user_id not in members_dict:
                    members_dict[m.user_id] = (m.username, m.first_name)
    except Exception as e:
        logger.warning("Could not load group members from DB: %s", e)

    # Also capture active lobby players
    manager = get_game_manager()
    game = manager.get_game(chat.id)
    if game:
        for p_id, p in game.participants.items():
            if p_id not in members_dict:
                members_dict[p_id] = (p.username, p.name)

    # If sender not in dict, add them
    if user and user.id not in members_dict:
        members_dict[user.id] = (user.username, user.first_name)

    member_list = [(uid, uname, fname) for uid, (uname, fname) in members_dict.items()]

    announcement = (
        html.escape(custom_text)
        if custom_text
        else "🎯 <b>The quiz battle is about to begin!</b> Join now to test your knowledge! 🚀"
    )

    lobby_button = None
    if game and game.status == "lobby":
        lobby_button = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🎮 Join Quiz Battle ({len(game.participants)})", callback_data=f"game_join_{game.quiz_set_id}")],
        ])

    task = asyncio.create_task(
        run_mention_sequence(
            bot=context.bot,
            chat_id=chat.id,
            members=member_list,
            announcement=announcement,
            reply_to_message_id=reply_to_id,
            lobby_button=lobby_button,
        )
    )
    active_mention_tasks[chat.id] = task


@require_group_admin
async def stop_mention_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop/cancel an ongoing mass mention sequence."""
    chat = update.effective_chat
    if not chat:
        return

    task = active_mention_tasks.get(chat.id)
    if task and not task.done():
        task.cancel()
        active_mention_tasks.pop(chat.id, None)
    else:
        if update.message:
            await update.message.reply_text("ℹ️ No active mention sequence running in this group.")


async def mention_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tag all group administrators (@admin, #admin, /admin)."""
    chat = update.effective_chat
    msg = update.message
    if not chat or not msg:
        return

    chat_type = getattr(chat, "type", None)
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"):
        await msg.reply_text("ℹ️ The <code>/admin</code> command can only be used in groups!", parse_mode=ParseMode.HTML)
        return

    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except Exception as e:
        logger.warning("Could not fetch admins in chat %s: %s", chat.id, e)
        return

    admin_tags = []
    for a in admins:
        if a.user and not a.user.is_bot:
            if a.user.username:
                admin_tags.append(f"@{a.user.username}")
            else:
                admin_tags.append(f'<a href="tg://user?id={a.user.id}">{html.escape(a.user.first_name)}</a>')

    if not admin_tags:
        await msg.reply_text("ℹ️ No human administrators found.")
        return

    reply_to_id = msg.reply_to_message.message_id if msg.reply_to_message else msg.message_id
    raw_text = msg.text or ""
    parts = raw_text.split(maxsplit=1)
    note = f"\n\n💬 <i>\"{html.escape(parts[1].strip())}\"</i>" if len(parts) > 1 else ""

    text = f"🚨 <b>Admin Attention Required:</b>{note}\n\n" + " ".join(admin_tags)
    await context.bot.send_message(
        chat_id=chat.id,
        text=text,
        reply_to_message_id=reply_to_id,
        parse_mode=ParseMode.HTML,
    )
