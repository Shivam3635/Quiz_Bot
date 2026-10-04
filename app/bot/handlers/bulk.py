import html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.keyboards.bulk import (
    get_description_input_keyboard,
    get_multipart_input_keyboard,
    get_parse_error_keyboard,
    get_session_conflict_keyboard,
    get_title_input_keyboard,
    get_validation_error_keyboard,
)
from app.bot.states import QuizCreationState
from app.database.database import SessionLocal
from app.database.repositories import get_or_create_user, save_quiz_batch
from app.parser.models import QuizSettings
from app.parser.parser import QuizBotProParser
from app.services.quiz_service import QuizService
from app.services.session_service import SessionManager
from app.utils.logger import setup_logger

logger = setup_logger(__name__)
quiz_service = QuizService()
session_manager = SessionManager()
fast_parser = QuizBotProParser()

TITLE_PROMPT_MESSAGE = (
    "✨ <b>Let's create a new quiz!</b>\n\n"
    "First, send me a <b>title</b> for your quiz.\n"
    "<i>(e.g. <code>General Science Mock Test #12</code>)</i>"
)

DESC_PROMPT_MESSAGE = (
    "👍 <b>Good.</b> Now send me a <b>description</b> for your quiz.\n\n"
    "This is optional, you can send /skip."
)


def format_bulk_prompt_message(settings: QuizSettings) -> str:
    """Format the bulk question input prompt incorporating title & description."""
    title_str = f"<b>{settings.title}</b>" if settings.title else "<i>Untitled Quiz</i>"
    desc_str = f"\n📝 Description: <i>{settings.description}</i>" if settings.description else ""
    return (
        f"📝 <b>Bulk Quiz Creation</b>\n\n"
        f"🏷️ Title: {title_str}{desc_str}\n\n"
        "Send your quiz questions in multiple messages.\n"
        "You can send 10, 20, 50, 100+ questions across as many messages as needed!\n\n"
        "<b>Example:</b>\n"
        "Message 1 → Q1–Q10\n"
        "Message 2 → Q11–Q20\n"
        "Message 3 → Q21–Q30\n\n"
        "I'll automatically combine all parts in sequence into one quiz.\n\n"
        "<i>When you have sent all questions, press:</i>\n"
        "<b>✅ Done</b>"
    )


async def start_quiz_session_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start or resume a multi-part quiz creation session by prompting for title."""
    user = update.effective_user
    chat = update.effective_chat
    user_id = user.id if user else 0
    chat_id = chat.id if chat else 0

    query = update.callback_query
    if query:
        await query.answer()

    # Check for active unfinished quiz session or draft
    existing_session = session_manager.get_active_session(user_id, chat_id)
    has_active_draft = (
        context.user_data.get("quiz_settings") is not None
        or (existing_session and existing_session.status in ("WAITING_FOR_INPUT", "PROCESSING"))
    )
    if has_active_draft:
        text = "You have an unfinished quiz. Please finish creating your quiz or send /cancel to discard it."
        if query and query.message:
            await query.edit_message_text(text)
        elif update.message:
            await update.message.reply_text(text)
        if existing_session and existing_session.total_parts > 0:
            return QuizCreationState.WAITING_FOR_BULK_INPUT
        if context.user_data.get("quiz_settings") and context.user_data.get("quiz_settings").title:
            return QuizCreationState.WAITING_FOR_DESCRIPTION
        return QuizCreationState.WAITING_FOR_TITLE

    # Reset / initialize fresh settings for the new quiz
    settings = QuizSettings()
    context.user_data["quiz_settings"] = settings
    context.user_data.pop("active_session_id", None)
    context.user_data.pop("bulk_questions", None)

    # Prompt for Title first without inline buttons (clean text prompt like @QuizBot)
    if query and query.message:
        await query.edit_message_text(
            TITLE_PROMPT_MESSAGE,
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            TITLE_PROMPT_MESSAGE,
            parse_mode=ParseMode.HTML,
        )

    return QuizCreationState.WAITING_FOR_TITLE


async def receive_quiz_title_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive quiz title text and advance to description prompt."""
    if not update.message or not update.message.text:
        return QuizCreationState.WAITING_FOR_TITLE

    title_text = update.message.text.strip()
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.title = title_text[:150]
    context.user_data["quiz_settings"] = settings

    await update.message.reply_text(
        f"🏷️ Quiz title set to: <b>{settings.title}</b>\n\n{DESC_PROMPT_MESSAGE}",
        parse_mode=ParseMode.HTML,
    )
    return QuizCreationState.WAITING_FOR_DESCRIPTION


async def skip_quiz_title_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip quiz title and advance to description prompt."""
    query = update.callback_query
    if query:
        await query.answer()

    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.title = None
    context.user_data["quiz_settings"] = settings

    if query and query.message:
        await query.edit_message_text(
            DESC_PROMPT_MESSAGE,
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            DESC_PROMPT_MESSAGE,
            parse_mode=ParseMode.HTML,
        )
    return QuizCreationState.WAITING_FOR_DESCRIPTION


async def receive_quiz_desc_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive quiz description text and initiate question collection session."""
    if not update.message or not update.message.text:
        return QuizCreationState.WAITING_FOR_DESCRIPTION

    desc_text = update.message.text.strip()
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.description = desc_text[:500]
    context.user_data["quiz_settings"] = settings

    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0

    session = session_manager.create_session(user_id, chat_id)
    context.user_data["active_session_id"] = session.session_id

    prompt_text = "👍 Good! Now send your quiz questions."
    await update.message.reply_text(
        prompt_text,
        parse_mode=ParseMode.HTML,
    )
    return QuizCreationState.WAITING_FOR_BULK_INPUT


async def skip_quiz_desc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip quiz description and initiate question collection session."""
    query = update.callback_query
    if query:
        await query.answer()

    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.description = None
    context.user_data["quiz_settings"] = settings

    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0

    session = session_manager.create_session(user_id, chat_id)
    context.user_data["active_session_id"] = session.session_id

    prompt_text = "👍 Good! Now send your quiz questions."
    if query and query.message:
        await query.edit_message_text(
            prompt_text,
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            prompt_text,
            parse_mode=ParseMode.HTML,
        )
    return QuizCreationState.WAITING_FOR_BULK_INPUT


async def continue_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Resume current session upon user choice."""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0

    session = session_manager.get_active_session(user_id, chat_id)
    if not session or session.total_parts == 0:
        return await start_quiz_session_flow(update, context)

    status_text = (
        "📦 <b>Current Quiz Session Status</b>\n\n"
        f"• Parts received: <b>{session.total_parts}</b>\n"
        f"• Questions detected: <b>{session.total_questions_detected}</b>\n"
        f"• Characters received: <b>{session.total_characters}</b>\n\n"
        "<i>Send another message with more questions, or press ✅ Done:</i>"
    )

    if query and query.message:
        await query.edit_message_text(
            status_text,
            reply_markup=get_multipart_input_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    return QuizCreationState.WAITING_FOR_BULK_INPUT


async def discard_and_restart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Discard active session and start completely fresh."""
    query = update.callback_query
    if query:
        await query.answer("Previous session cleared.")

    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0

    session_manager.discard_session(user_id, chat_id)
    context.user_data.pop("active_session_id", None)
    context.user_data.pop("bulk_questions", None)
    context.user_data.pop("quiz_settings", None)

    return await start_quiz_session_flow(update, context)


async def receive_quiz_part_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Collect an incoming message as a chunk/part of the current quiz session."""
    if not update.message or not update.message.text:
        return QuizCreationState.WAITING_FOR_BULK_INPUT

    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    raw_text = update.message.text.strip()

    # 1. Retrieve or verify session
    session = session_manager.get_active_session(user_id, chat_id)
    if not session:
        # Check if expired
        session = session_manager.create_session(user_id, chat_id)
        context.user_data["active_session_id"] = session.session_id

    # 2. Check processing lock
    if session.is_processing_lock or session.status == "PROCESSING":
        await update.message.reply_text(
            "⏳ <i>Your quiz is currently being processed. Please wait for the current operation to finish.</i>",
            parse_mode=ParseMode.HTML,
        )
        return QuizCreationState.WAITING_FOR_BULK_INPUT

    # 3. Detect questions in this chunk
    parsed = fast_parser.parse(raw_text)
    detected_count = len(parsed.questions)

    # If completely empty or non-quiz content (e.g. "hello", "next part")
    if detected_count == 0 and parsed.total_blocks_found == 0:
        await update.message.reply_text(
            "⚠️ <b>No quiz questions were detected in this message.</b>\n\n"
            "Please send a message containing your questions (e.g. Q1... A... Answer: B).",
            reply_markup=get_multipart_input_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return QuizCreationState.WAITING_FOR_BULK_INPUT

    # 4. Append part in strict sequential order
    part = session.add_part(raw_text, detected_questions=detected_count)
    logger.info(
        "User %s sent Part %d (%d detected questions, %d chars). Total parts: %d",
        user_id,
        part.part_number,
        part.detected_questions,
        part.character_count,
        session.total_parts,
    )

    ack_message = "Send more questions, or send /done to finish creating the quiz."

    # Clean up previous status message to prevent chat clutter
    prev_msg_id = context.user_data.get("last_part_status_msg_id")
    if prev_msg_id and update.effective_chat:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=prev_msg_id)
        except Exception:
            pass

    sent_msg = await update.message.reply_text(
        ack_message,
        parse_mode=ParseMode.HTML,
    )
    context.user_data["last_part_status_msg_id"] = sent_msg.message_id

    return QuizCreationState.WAITING_FOR_BULK_INPUT


async def multipart_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process all collected parts together into one unified quiz."""
    query = update.callback_query
    msg = update.message
    if query:
        await query.answer()

    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0

    session = session_manager.get_active_session(user_id, chat_id)
    if not session:
        if query:
            await query.answer("Session expired or not found. Please start with /newquiz.")
        elif msg:
            await msg.reply_text("Session expired or not found. Please start with /newquiz.")
        return await start_quiz_session_flow(update, context)

    # Duplicate Done protection lock
    if session.is_processing_lock or session.status == "PROCESSING":
        if query:
            await query.answer("⏳ Processing already in progress...")
        elif msg:
            await msg.reply_text("⏳ Processing already in progress...")
        return QuizCreationState.WAITING_FOR_BULK_INPUT

    # Check if empty
    if session.total_parts == 0:
        no_parts_text = (
            "⚠️ <b>No quiz questions received yet.</b>\n\n"
            "Please send at least one message containing your questions before sending /done."
        )
        if query and query.message:
            await query.edit_message_text(
                no_parts_text,
                reply_markup=get_multipart_input_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        elif msg:
            await msg.reply_text(
                no_parts_text,
                reply_markup=get_multipart_input_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        return QuizCreationState.WAITING_FOR_BULK_INPUT

    # Acquire lock and set status to PROCESSING
    session.is_processing_lock = True
    session.status = "PROCESSING"
    if query:
        await query.answer("Processing quiz parts...")

    processing_text = (
        "⏳ <b>Processing your quiz...</b>\n\n"
        f"• Parts received: <b>{session.total_parts}</b>\n"
        f"• Questions detected: <b>{session.total_questions_detected}</b>\n\n"
        "<i>Combining all parts and validating format, please wait...</i>"
    )
    status_msg = None
    if query and query.message:
        status_msg = query.message
        await query.edit_message_text(processing_text, parse_mode=ParseMode.HTML)
    elif msg:
        status_msg = await msg.reply_text(processing_text, parse_mode=ParseMode.HTML)

    # Combine all parts in exact sequential order with double newlines
    combined_text = session.combine_parts_text()
    result = quiz_service.process_raw_text(combined_text)

    # Release processing lock
    session.is_processing_lock = False

    # Case 1: Parsing errors
    if result.raw_batch.has_errors:
        session.status = "WAITING_FOR_INPUT"
        err_lines = [
            "❌ <b>Quiz processing encountered issues.</b>\n",
            f"Parts combined: <b>{session.total_parts}</b>",
            f"Detected questions: <b>{result.raw_batch.valid_count}</b>",
            f"Errors: <b>{result.raw_batch.error_count}</b>\n",
            "<b>Problems identified:</b>",
        ]
        for err in result.raw_batch.errors[:4]:
            q_label = f"Question {err.question_number}" if err.question_number else "Question"
            err_lines.append(f"• <b>{q_label}</b>: {err.message}")

        err_lines.append("\nYou can send more parts to fix, or discard and start again:")

        if status_msg:
            await status_msg.edit_text(
                "\n".join(err_lines),
                reply_markup=get_parse_error_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        return QuizCreationState.WAITING_FOR_BULK_INPUT

    # Case 2: Validation errors against Telegram limits
    if not result.validation.is_valid:
        session.status = "PREVIEW"
        questions = result.raw_batch.questions
        settings = QuizSettings()

        context.user_data["bulk_questions"] = questions
        context.user_data["quiz_settings"] = settings

        first_invalid_idx = (
            result.validation.failed_question_indices[0]
            if result.validation.failed_question_indices
            else 1
        )
        context.user_data["preview_index"] = max(0, first_invalid_idx - 1)

        custom_footer = (
            "💡 <i>You can fix these questions right now using the in-app editor,\n"
            "drop the invalid ones, or send additional parts:</i>"
        )
        report = result.validation.get_formatted_error_report(max_display=4, custom_footer=custom_footer)

        unique_failed = len(result.validation.failed_question_indices)
        msg_text = (
            f"❌ <b>Validation Notice</b>\n\n"
            f"• Parts combined: <b>{session.total_parts}</b>\n"
            f"• Total questions: <b>{result.validation.total_questions}</b>\n"
            f"• Issues needing fix: <b>{result.validation.error_count}</b>\n\n"
            f"{report}"
        )

        val_keyboard = get_validation_error_keyboard(
            first_invalid_idx=first_invalid_idx,
            total_valid=result.validation.valid_count,
            total_invalid=unique_failed,
        )

        if status_msg:
            await status_msg.edit_text(
                msg_text,
                reply_markup=val_keyboard,
                parse_mode=ParseMode.HTML,
            )
        return QuizCreationState.PREVIEWING

    # Case 3: Success! All parts parsed and validated
    session.status = "COMPLETED"
    questions = result.questions
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()

    context.user_data["bulk_questions"] = questions
    context.user_data["quiz_settings"] = settings
    context.user_data["preview_index"] = 0

    user = update.effective_user
    user_id = user.id if user else 0
    saved_quiz_set_id = None
    try:
        with SessionLocal() as db:
            user_rec = get_or_create_user(db, telegram_id=user_id, username=user.username if user else None)
            quiz_set = save_quiz_batch(
                db=db,
                user_id=user_rec.id,
                title=settings.title,
                description=settings.description,
                questions=questions,
                settings=settings,
            )
            saved_quiz_set_id = quiz_set.id
            context.user_data["saved_quiz_set_id"] = saved_quiz_set_id
            logger.info("Successfully persisted quiz set %d for user %d", saved_quiz_set_id, user_id)
    except Exception as db_err:
        logger.exception("Failed to save quiz batch for user %s: %s", user_id, db_err)
        session.status = "WAITING_FOR_INPUT"
        if status_msg:
            await status_msg.edit_text(
                "❌ <b>Database Error</b>\n\n"
                "An unexpected database error occurred while saving your quiz. Please try sending /done again or /newquiz to start fresh.",
                parse_mode=ParseMode.HTML,
            )
        return QuizCreationState.WAITING_FOR_BULK_INPUT

    from app.bot.keyboards.settings import get_quiz_settings_config_keyboard
    from app.bot.handlers.settings import format_quiz_settings_text

    settings_text = format_quiz_settings_text(settings, q_count=len(questions))
    settings_kb = get_quiz_settings_config_keyboard(settings, quiz_set_id=saved_quiz_set_id)

    if status_msg:
        await status_msg.edit_text(
            settings_text,
            reply_markup=settings_kb,
            parse_mode=ParseMode.HTML,
        )

    return QuizCreationState.CONFIGURING_SETTINGS


async def creation_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save quiz with selected timer and display the unified Quiz Card."""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()

    timer_val = int(query.data.split("_")[-1])
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.time_limit = timer_val if timer_val > 0 else None
    context.user_data["quiz_settings"] = settings

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    user = update.effective_user
    chat = update.effective_chat
    user_id = user.id if user else 0
    chat_id = chat.id if chat else 0

    quiz_set_id = None
    try:
        with SessionLocal() as db:
            db_user = get_or_create_user(
                db,
                telegram_id=user_id,
                username=user.username if user else None,
            )
            quiz_set = save_quiz_batch(
                db,
                user_id=db_user.id,
                questions=questions,
                settings=settings,
                title=settings.title if settings.title else f"Batch of {len(questions)} Questions",
                description=settings.description,
            )
            quiz_set_id = quiz_set.id
            context.user_data["saved_quiz_set_id"] = quiz_set.id
            logger.info("Quiz set %d persisted on creation for user %d", quiz_set.id, user_id)
    except Exception as db_err:
        logger.error("Could not persist quiz batch on creation: %s", db_err)

    session_manager.discard_session(user_id, chat_id)

    from app.bot.keyboards.myquizzes import get_quiz_details_keyboard

    title_display = f"<b>{html.escape(settings.title)}</b>" if settings.title else "<i>Untitled Quiz</i>"
    desc_display = f"\n• <b>Description:</b> <i>{html.escape(settings.description)}</i>" if settings.description else ""
    timer_str = f"{settings.time_limit}s" if settings.time_limit else "No limit"
    target_str = settings.channel_id if settings.channel_id else "This chat (Default)"

    unified_card_text = (
        "🎉 <b>Quiz Creation Successful!</b>\n\n"
        f"📊 <b>Quiz Details: {title_display}</b>\n\n"
        f"• <b>Total Questions:</b> <code>{len(questions)}</code>"
        f"{desc_display}\n"
        f"• <b>Timer:</b> <code>{timer_str}</code>\n"
        f"• <b>Target Destination:</b> <b>{target_str}</b>\n"
        f"• <b>Mode:</b> <code>{'Anonymous' if settings.is_anonymous else 'Public'}</code>\n"
        f"• <b>Explanations:</b> <code>{'Enabled' if settings.explanation_enabled else 'Disabled'}</code>\n\n"
        "💾 <i>Your quiz is saved safely! You can publish it now or access it anytime via /myquizzes:</i>"
    )

    bot_username = context.bot.username if context.bot else None
    keyboard = get_quiz_details_keyboard(quiz_set_id, bot_username=bot_username)

    if query.message:
        await query.edit_message_text(
            unified_card_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )

    return ConversationHandler.END


async def cancel_quiz_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Safely cancel quiz creation session and return to main menu."""
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0

    session_manager.discard_session(user_id, chat_id)
    context.user_data.pop("active_session_id", None)
    context.user_data.pop("bulk_questions", None)
    context.user_data.pop("quiz_settings", None)
    context.user_data.pop("preview_index", None)

    from app.bot.handlers.start import WELCOME_MESSAGE
    from app.bot.keyboards.main import get_main_menu_keyboard

    query = update.callback_query
    if query:
        await query.answer("Quiz creation cancelled.")
        await query.edit_message_text(
            f"❌ <b>Quiz creation cancelled.</b>\n\n{WELCOME_MESSAGE}",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            f"❌ <b>Quiz creation cancelled.</b>\n\n{WELCOME_MESSAGE}",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )

    return ConversationHandler.END
