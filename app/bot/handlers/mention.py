"""Handlers for @MentionBot-style mass group mentions (/all, @all, /admin)."""

import asyncio
import html
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest
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
    """Silently record active group members into the database for mass mentions.

    Tracks messages, commands, new members joining, replied-to users, and text mentions.
    """
    chat = update.effective_chat
    user = update.effective_user

    if not chat:
        return

    chat_type = getattr(chat, "type", None)
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"):
        return

    seen = None
    if context.chat_data is not None:
        seen = context.chat_data.setdefault("seen_members", {})

    try:
        with SessionLocal() as db:
            # 1. Track sender
            if user and not user.is_bot:
                record_group_member(
                    db=db,
                    chat_id=chat.id,
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                )
                if seen is not None:
                    seen[user.id] = (user.username, user.first_name)

            # 2. Track new chat members (when added or joined)
            if update.message and update.message.new_chat_members:
                for new_u in update.message.new_chat_members:
                    if not new_u.is_bot:
                        record_group_member(
                            db=db,
                            chat_id=chat.id,
                            user_id=new_u.id,
                            username=new_u.username,
                            first_name=new_u.first_name,
                        )
                        if seen is not None:
                            seen[new_u.id] = (new_u.username, new_u.first_name)

            # 3. Track replied-to user
            if (
                update.message
                and update.message.reply_to_message
                and update.message.reply_to_message.from_user
            ):
                reply_u = update.message.reply_to_message.from_user
                if not reply_u.is_bot:
                    record_group_member(
                        db=db,
                        chat_id=chat.id,
                        user_id=reply_u.id,
                        username=reply_u.username,
                        first_name=reply_u.first_name,
                    )
                    if seen is not None:
                        seen[reply_u.id] = (reply_u.username, reply_u.first_name)

            # 4. Track text-mentioned users in entities
            if update.message and update.message.entities:
                for ent in update.message.entities:
                    if ent.type == "text_mention" and ent.user and not ent.user.is_bot:
                        record_group_member(
                            db=db,
                            chat_id=chat.id,
                            user_id=ent.user.id,
                            username=ent.user.username,
                            first_name=ent.user.first_name,
                        )
                        if seen is not None:
                            seen[ent.user.id] = (ent.user.username, ent.user.first_name)
    except Exception as e:
        logger.debug("Failed to record group member update in chat %s: %s", chat.id, e)


async def handle_chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track when users join, leave, or change status in the group."""
    chat_member = update.chat_member or update.my_chat_member
    if not chat_member:
        return

    chat = chat_member.chat
    user = chat_member.new_chat_member.user
    if not chat or not user or user.is_bot:
        return

    chat_type = getattr(chat, "type", None)
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"):
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
                if context.chat_data is not None:
                    context.chat_data.setdefault("seen_members", {})[user.id] = (user.username, user.first_name)
            elif new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                deactivate_group_member(db=db, chat_id=chat.id, user_id=user.id)
                if context.chat_data is not None and "seen_members" in context.chat_data:
                    context.chat_data["seen_members"].pop(user.id, None)
    except Exception as e:
        logger.debug("Failed to handle chat member update for %s: %s", user.id, e)


async def handle_my_chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bot added to a group or promoted to administrator."""
    chat_member = update.my_chat_member
    if not chat_member:
        return

    chat = chat_member.chat
    if not chat:
        return

    chat_type = getattr(chat, "type", None)
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"):
        return

    new_status = chat_member.new_chat_member.status
    if new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            with SessionLocal() as db:
                for admin in admins:
                    if admin.user and not admin.user.is_bot:
                        record_group_member(
                            db=db,
                            chat_id=chat.id,
                            user_id=admin.user.id,
                            username=admin.user.username,
                            first_name=admin.user.first_name,
                        )
                        if context.chat_data is not None:
                            context.chat_data.setdefault("seen_members", {})[admin.user.id] = (
                                admin.user.username,
                                admin.user.first_name,
                            )
            logger.info("Successfully seeded administrators for chat %s on bot join.", chat.id)
        except Exception as e:
            logger.debug("Failed to seed group admins on bot join in chat %s: %s", chat.id, e)


async def tag_me_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow group members to self-register into the mention roster with one tap."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat

    if not query or not user or not chat:
        return

    if user.is_bot:
        await query.answer("Bots cannot be added to the mention roster.", show_alert=True)
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
        if context.chat_data is not None:
            context.chat_data.setdefault("seen_members", {})[user.id] = (user.username, user.first_name)

        display_name = user.first_name or user.username or "Player"
        await query.answer(f"✅ Awesome, {display_name}! You are registered in the group mention list.", show_alert=True)
    except Exception as e:
        logger.warning("Error registering user %s in chat %s: %s", user.id, chat.id, e)
        await query.answer("✅ You are now registered on the mention list!")


async def mention_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for 'Stop Mentions' inline button."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat:
        return

    if not await is_user_group_admin(context.bot, chat, user.id):
        await query.answer("Only group admins can stop the mention sequence!", show_alert=True)
        return

    task = active_mention_tasks.get(chat.id)
    if task and not task.done():
        task.cancel()
        active_mention_tasks.pop(chat.id, None)
        await query.answer("Mention sequence stopped! 🛑")
    else:
        await query.answer("No active mention sequence running.")


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
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=initial_text,
                reply_markup=lobby_button,
                reply_to_message_id=reply_to_message_id,
                parse_mode=ParseMode.HTML,
            )
        except BadRequest as e:
            if any(kw in str(e).lower() for kw in ("reply", "repli")):
                reply_to_message_id = None
                await bot.send_message(
                    chat_id=chat_id,
                    text=initial_text,
                    reply_markup=lobby_button,
                    parse_mode=ParseMode.HTML,
                )
            else:
                raise

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
            if not batch_text.strip():
                continue

            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=batch_text,
                    reply_to_message_id=reply_to_message_id,
                    parse_mode=ParseMode.HTML,
                )
            except BadRequest as e:
                if any(kw in str(e).lower() for kw in ("reply", "repli")):
                    reply_to_message_id = None
                    await bot.send_message(
                        chat_id=chat_id,
                        text=batch_text,
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    logger.warning("BadRequest during mention batch in chat %s: %s", chat_id, e)
            except Exception as batch_err:
                logger.warning("Error sending mention batch in chat %s: %s", chat_id, batch_err)

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

    # Track total chat members count from Telegram
    total_group_members: Optional[int] = None
    try:
        total_group_members = await context.bot.get_chat_member_count(chat.id)
    except Exception as e:
        logger.debug("Could not fetch total member count for chat %s: %s", chat.id, e)

    members_dict: dict[int, tuple[Optional[str], Optional[str]]] = {}

    # 1. Fetch administrators from Telegram API and seed them into DB
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        with SessionLocal() as db:
            for admin in admins:
                if admin.user and not admin.user.is_bot:
                    members_dict[admin.user.id] = (admin.user.username, admin.user.first_name)
                    record_group_member(
                        db=db,
                        chat_id=chat.id,
                        user_id=admin.user.id,
                        username=admin.user.username,
                        first_name=admin.user.first_name,
                    )
    except Exception as e:
        logger.warning("Could not fetch administrators for chat %s: %s", chat.id, e)

    # 2. Fetch saved group members from DB
    try:
        with SessionLocal() as db:
            db_members = get_group_members(db, chat.id)
            for m in db_members:
                if m.user_id not in members_dict:
                    members_dict[m.user_id] = (m.username, m.first_name)
    except Exception as e:
        logger.warning("Could not load group members from DB: %s", e)

    # 3. Fetch from context.chat_data seen_members cache
    if context.chat_data is not None and "seen_members" in context.chat_data:
        for u_id, (uname, fname) in context.chat_data["seen_members"].items():
            if u_id not in members_dict:
                members_dict[u_id] = (uname, fname)

    # 4. Capture active game participants
    manager = get_game_manager()
    game = manager.get_game(chat.id)
    if game:
        for p_id, p in game.scores.items():
            if p_id not in members_dict:
                members_dict[p_id] = (p.username, p.full_name)

    # 5. Record the sender (caller) into DB
    if user and not user.is_bot:
        try:
            with SessionLocal() as db:
                record_group_member(
                    db=db,
                    chat_id=chat.id,
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                )
        except Exception:
            pass

    # Exclude sender from being mentioned if other members exist (avoid self-pinging caller)
    target_members = {uid: info for uid, info in members_dict.items() if not (user and uid == user.id)}

    # If no other members are found yet:
    if not target_members:
        tag_me_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🙋 Tag Me / Register for @all", callback_data="mention_tag_me")]
        ])
        help_note = (
            "⚠️ <b>No other group members registered yet!</b>\n\n"
            "Telegram Bot API does not permit bots to read silent group members directly.\n\n"
            "👉 <b>How members get added:</b>\n"
            "• Sending any message or command in this group\n"
            "• Joining the group\n"
            "• Clicking the button below:\n"
        )
        await msg.reply_text(help_note, reply_markup=tag_me_keyboard, parse_mode=ParseMode.HTML)
        return

    member_list = [(uid, uname, fname) for uid, (uname, fname) in target_members.items()]

    # Construct announcement text with count stats
    body = (
        html.escape(custom_text)
        if custom_text
        else "🎯 <b>The quiz battle is about to begin!</b> Join now to test your knowledge! 🚀"
    )

    count_str = f"👥 <b>Tagging:</b> <code>{len(member_list)}</code> members"
    if total_group_members and total_group_members > 1:
        # Subtract 1 for the bot itself
        actual_total = total_group_members - 1
        count_str += f" (Group members: <code>{actual_total}</code>)"

    announcement = f"{body}\n\n{count_str}"
    if total_group_members and len(member_list) < (total_group_members - 1):
        announcement += "\n💡 <i>Not tagged? Tap '🙋 Tag Me' below or send any message!</i>"

    # Action buttons: Lobby button (if active) + Tag Me button + Stop button
    buttons = []
    if game and game.status == "lobby":
        buttons.append([InlineKeyboardButton(f"🎮 Join Quiz Battle ({len(game.scores)})", callback_data=f"game_join_{game.quiz_set_id}")])
    buttons.append([InlineKeyboardButton("🙋 Tag Me / Register for @all", callback_data="mention_tag_me")])
    buttons.append([InlineKeyboardButton("🛑 Stop Mentions", callback_data="mention_stop")])

    action_markup = InlineKeyboardMarkup(buttons)

    task = asyncio.create_task(
        run_mention_sequence(
            bot=context.bot,
            chat_id=chat.id,
            members=member_list,
            announcement=announcement,
            reply_to_message_id=reply_to_id,
            lobby_button=action_markup,
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
