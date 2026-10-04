"""Handlers for Live Group Quiz Mode, Lobby, and Real-Time Leaderboards."""

import asyncio
import html
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from app.bot.keyboards.game import (
    get_game_lobby_keyboard,
    get_select_quiz_for_game_keyboard,
)
from app.database.database import SessionLocal
from app.database.repositories import get_quiz_set_by_id, get_user_quiz_sets, reconstruct_quiz_data
from app.services.game_service import GameSession, get_game_manager, run_game_loop
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


async def is_user_group_admin(bot, chat, user_id: int) -> bool:
    """Return True if in private chat or user is group admin/creator."""
    if not chat or chat.type in (ChatType.PRIVATE, "private"):
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status in ("creator", "administrator")
    except Exception as e:
        logger.warning("Could not check admin status for user %s in chat %s: %s", user_id, getattr(chat, 'id', 'unknown'), e)
        return False


async def launch_game_lobby(
    chat_id: int,
    chat_title: Optional[str],
    quiz_set_id: int,
    initiator_id: int,
    initiator_name: str,
    bot,
    target_message=None,
) -> Optional[GameSession]:
    """Initialize a game session and display the lobby card in the chat."""
    manager = get_game_manager()

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id)
        if not quiz_set or not quiz_set.questions:
            msg = "⚠️ Quiz set not found or contains no questions."
            if target_message:
                await target_message.reply_text(msg)
            else:
                await bot.send_message(chat_id=chat_id, text=msg)
            return None

        questions, settings = reconstruct_quiz_data(quiz_set)

    time_limit = settings.time_limit if (settings.time_limit and 5 <= settings.time_limit <= 600) else 15

    game = manager.create_game(
        chat_id=chat_id,
        chat_title=chat_title,
        quiz_set_id=quiz_set_id,
        title=quiz_set.title,
        description=quiz_set.description,
        questions=questions,
        time_limit=time_limit,
        initiator_id=initiator_id,
        initiator_name=initiator_name,
    )

    lobby_text = game.format_lobby_text()
    keyboard = get_game_lobby_keyboard(quiz_set_id)

    if target_message:
        sent_msg = await target_message.reply_text(
            lobby_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    else:
        sent_msg = await bot.send_message(
            chat_id=chat_id,
            text=lobby_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )

    game.lobby_message_id = sent_msg.message_id
    logger.info("Launched quiz battle lobby in chat %s for quiz %d", chat_id, quiz_set_id)
    return game


async def startquiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /startquiz [quiz_id] in both private chats and groups."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    manager = get_game_manager()
    args = context.args or []

    # 1. Private chat behavior: guide user to start in a group
    if chat.type == ChatType.PRIVATE:
        if args and args[0].isdigit():
            quiz_id = int(args[0])
            bot_username = context.bot.username or "QuizBotPro"
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎮 Add to Group & Start Battle",
                        url=f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}",
                    )
                ],
                [
                    InlineKeyboardButton("🔙 Back to My Quizzes", callback_data="my_quizzes_list_0")
                ]
            ])
            await update.message.reply_text(
                f"🎯 <b>Live Group Quiz Mode</b>\n\n"
                f"To run <b>Quiz #{quiz_id}</b> with your friends or students:\n"
                f"1. Tap the button below to add this bot to your Telegram group.\n"
                f"2. The bot will automatically launch the quiz battle lobby in your group!\n\n"
                f"Or inside any group where the bot is added, simply type:\n"
                f"<code>/startquiz {quiz_id}</code>",
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return

        # Show user's quizzes to pick from
        with SessionLocal() as db:
            quiz_sets, total = get_user_quiz_sets(db, telegram_id=user.id, limit=10)

        if not quiz_sets:
            await update.message.reply_text(
                "⚠️ You don't have any saved quizzes yet. Use /newquiz to create one first!",
                parse_mode=ParseMode.HTML,
            )
            return

        bot_username = context.bot.username or "QuizBotPro"
        kb_rows = []
        for qs in quiz_sets:
            kb_rows.append([
                InlineKeyboardButton(
                    f"🎮 {qs.title[:22]} ({len(qs.questions)} Qs)",
                    url=f"https://t.me/{bot_username}?startgroup=quiz_{qs.id}",
                )
            ])
        await update.message.reply_text(
            "🎮 <b>Select a Quiz to Play in a Group:</b>\n\n"
            "Choose a quiz below to add the bot to a group and start a live competition:",
            reply_markup=InlineKeyboardMarkup(kb_rows),
            parse_mode=ParseMode.HTML,
        )
        return

    # 2. Group chat behavior:
    # Requirement 8: Quiz can only be posted and controlled by the group Admin
    if not await is_user_group_admin(context.bot, chat, user.id):
        await update.message.reply_text("⚠️ Only group administrators can start a quiz battle in this group.")
        return

    # Check if a game is already active in this group
    existing_game = manager.get_game(chat.id)
    if existing_game and existing_game.status in ("lobby", "running", "countdown"):
        await update.message.reply_text(
            f"⚠️ A quiz battle (<b>{html.escape(existing_game.title)}</b>) is already active in this group!\n\n"
            "Use /stopquiz to terminate it first.",
            parse_mode=ParseMode.HTML,
        )
        return

    if args and args[0].isdigit():
        quiz_id = int(args[0])
        user_name = user.username or user.first_name
        await launch_game_lobby(
            chat_id=chat.id,
            chat_title=chat.title,
            quiz_set_id=quiz_id,
            initiator_id=user.id,
            initiator_name=user_name,
            bot=context.bot,
            target_message=update.message,
        )
        return

    # No quiz ID provided in group: let host select from their quizzes
    with SessionLocal() as db:
        user_quizzes, _ = get_user_quiz_sets(db, telegram_id=user.id, limit=5)

    if user_quizzes:
        await update.message.reply_text(
            f"👑 <b>{html.escape(user.first_name)}</b>, select a quiz to launch in this group:",
            reply_markup=get_select_quiz_for_game_keyboard(user_quizzes),
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "🎮 <b>Live Group Quiz Mode</b>\n\n"
            "To launch a quiz battle in this group, specify the Quiz ID:\n"
            "<code>/startquiz &lt;quiz_id&gt;</code>\n\n"
            "💡 <i>Tip: Create quizzes in private chat via /newquiz or check /myquizzes to find your Quiz ID!</i>",
            parse_mode=ParseMode.HTML,
        )


async def stopquiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stopquiz to terminate an ongoing game."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat:
        return

    manager = get_game_manager()
    game = manager.get_game(chat.id)

    if not game or game.status not in ("lobby", "running", "countdown"):
        if update.message:
            await update.message.reply_text("ℹ️ No active quiz battle in this group.")
        return

    # Requirement 8: Must be group admin to stop quiz
    if not await is_user_group_admin(context.bot, chat, user.id):
        if update.message:
            await update.message.reply_text("⚠️ Only group administrators can stop the quiz battle.")
        return

    user_name = user.first_name if user else "Admin"
    manager.stop_game(chat.id)

    if update.message:
        await update.message.reply_text(
            f"🛑 <b>Quiz Battle Stopped</b> by {html.escape(user_name)}.\n"
            "Use /startquiz to start a new competition anytime!",
            parse_mode=ParseMode.HTML,
        )


async def join_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle participant clicking 'Join Quiz' in group lobby."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat:
        return

    manager = get_game_manager()
    game = manager.get_game(chat.id)

    if not game or game.status != "lobby":
        await query.answer("⚠️ This quiz lobby is no longer active.", show_alert=True)
        return

    added = game.add_participant(
        user_id=user.id,
        full_name=user.first_name,
        username=user.username,
    )

    if not added:
        await query.answer("You already voted ready! 👍")
        return

    await query.answer("You are ready! 🎯")

    # Requirement 2: Auto-start countdown of 5 when at least 2 players voted I am ready
    if len(game.scores) >= 2 and game.status == "lobby":
        game.status = "countdown"
        countdown_steps = [
            "5... 🕒",
            "4... 🕑",
            "3... Ready 🕒",
            "2... Set 🕑",
            "1... Go! 🚀",
        ]
        for count in countdown_steps:
            try:
                await query.edit_message_text(
                    f"🎮 <b>Quiz Battle: {html.escape(game.title)}</b>\n\n"
                    f"👥 <b>2 players ready! Starting automatically...</b>\n\n"
                    f"🚀 <i>{count}</i>\n\n"
                    "<b>Get ready to answer the polls!</b>",
                    parse_mode=ParseMode.HTML,
                )
                await asyncio.sleep(1.0)
            except Exception as e:
                logger.debug("Countdown edit notice: %s", e)

        try:
            await query.edit_message_text(
                f"🏁 <b>Quiz Battle Started!</b>\n"
                f"🎯 <b>Quiz:</b> {html.escape(game.title)}\n"
                f"⚡ <i>First question incoming...</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.debug("Countdown final notice: %s", e)

        game.task = asyncio.create_task(run_game_loop(context.bot, game))
    else:
        try:
            quiz_set_id = int(query.data.split("_")[-1])
            await query.edit_message_text(
                game.format_lobby_text(),
                reply_markup=get_game_lobby_keyboard(quiz_set_id),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.debug("Minor edit lobby notice: %s", e)


async def start_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle initiator or admin clicking 'Start Battle'."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat:
        return

    manager = get_game_manager()
    game = manager.get_game(chat.id)

    if not game or game.status != "lobby":
        await query.answer("⚠️ This quiz lobby is no longer active.", show_alert=True)
        return

    # Requirement 6: Wait for at least 2 players/votes to start the quiz
    if len(game.scores) < 2:
        await query.answer("⚠️ At least 2 players must join before the quiz can start!", show_alert=True)
        return

    # Requirement 8: Only group admin can start the battle
    if not await is_user_group_admin(context.bot, chat, user.id):
        await query.answer("⚠️ Only a Group Admin can start the battle!", show_alert=True)
        return

    await query.answer("Starting battle! 🚀")
    game.status = "countdown"

    # Animated countdown
    for count in ["3... 🕒", "2... 🕑", "1... 🕐"]:
        try:
            await query.edit_message_text(
                f"🎮 <b>Quiz Battle: {html.escape(game.title)}</b>\n\n"
                f"🚀 <i>Starting in {count}</i>\n\n"
                "<b>Get ready to answer the polls!</b>",
                parse_mode=ParseMode.HTML,
            )
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.debug("Countdown edit notice: %s", e)

    try:
        await query.edit_message_text(
            f"🏁 <b>Quiz Battle Started!</b>\n"
            f"🎯 <b>Quiz:</b> {html.escape(game.title)}\n"
            f"⚡ <i>First question incoming...</i>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.debug("Countdown final notice: %s", e)

    # Launch game loop
    game.task = asyncio.create_task(run_game_loop(context.bot, game))


async def cancel_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancelling a quiz lobby."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat:
        return

    manager = get_game_manager()
    game = manager.get_game(chat.id)

    if not game:
        await query.answer("No active lobby to cancel.")
        return

    # Requirement 8: Only group admin can cancel the battle
    if not await is_user_group_admin(context.bot, chat, user.id):
        await query.answer("⚠️ Only a Group Admin can cancel the battle!", show_alert=True)
        return

    manager.stop_game(chat.id)
    await query.answer("Quiz battle cancelled.")
    await query.edit_message_text(
        f"❌ <b>Quiz Battle Cancelled</b> by {html.escape(user.first_name)}.",
        parse_mode=ParseMode.HTML,
    )


async def launch_selected_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quiz selection button from /startquiz list."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat or not query.data:
        return

    # Requirement 8: Only group admin can launch a quiz
    if not await is_user_group_admin(context.bot, chat, user.id):
        await query.answer("⚠️ Only group administrators can launch a quiz!", show_alert=True)
        return

    await query.answer()
    quiz_set_id = int(query.data.split("_")[-1])
    user_name = user.username or user.first_name

    await launch_game_lobby(
        chat_id=chat.id,
        chat_title=chat.title,
        quiz_set_id=quiz_set_id,
        initiator_id=user.id,
        initiator_name=user_name,
        bot=context.bot,
        target_message=query.message,
    )


async def replay_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'Play Again' button after a finished battle."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat or not query.data:
        return

    await query.answer("Setting up a new battle! 🔄")
    quiz_set_id = int(query.data.split("_")[-1])
    user_name = user.username or user.first_name

    await launch_game_lobby(
        chat_id=chat.id,
        chat_title=chat.title,
        quiz_set_id=quiz_set_id,
        initiator_id=user.id,
        initiator_name=user_name,
        bot=context.bot,
        target_message=query.message,
    )


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Real-time listener for user poll answer events."""
    poll_answer = update.poll_answer
    if not poll_answer or not poll_answer.user or not poll_answer.option_ids:
        return

    poll_id = poll_answer.poll_id
    user = poll_answer.user
    selected_option = poll_answer.option_ids[0]

    manager = get_game_manager()
    game = manager.get_game_by_poll(poll_id)

    if not game or game.status != "running":
        return

    current_q = game.questions[game.current_question_idx]
    game.record_answer(
        user_id=user.id,
        full_name=user.first_name,
        username=user.username,
        selected_option=selected_option,
        correct_option=current_q.correct_option,
    )
