"""Confirmation, bulk publishing execution, and retry handlers."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.keyboards.preview import get_publish_confirm_keyboard, get_retry_keyboard
from app.bot.keyboards.bulk import get_validation_error_keyboard
from app.bot.keyboards.main import get_main_menu_keyboard
from app.bot.states import QuizCreationState
from app.database.database import SessionLocal
from app.database.repositories import (
    complete_publish_job,
    create_publish_job,
    get_or_create_user,
    get_quiz_set_by_id,
    save_quiz_batch,
)
from app.parser.models import QuizQuestion, QuizSettings
from app.parser.validator import QuizValidator
from app.services.publishing_service import PublishingService, PublishingSummary
from app.utils.logger import setup_logger

logger = setup_logger(__name__)
publishing_service = PublishingService()
validator = QuizValidator()


def format_confirmation_overview(total: int, settings: QuizSettings) -> str:
    """Format the pre-publish confirmation summary."""
    title_display = settings.title if settings.title else "Untitled Batch"
    desc_display = f"\n• Description: <i>{settings.description}</i>" if settings.description else ""
    dest = settings.channel_id if settings.channel_id else "This chat (Current conversation)"
    banner = "ON ✅" if settings.header_banner_enabled else "OFF ❌"
    anon = "ON ✅" if settings.is_anonymous else "OFF ❌"
    timer = f"{settings.time_limit} sec" if settings.time_limit else "None"
    expl = "ON ✅" if settings.explanation_enabled else "OFF ❌"

    return (
        "🚀 <b>Ready to Publish Quizzes!</b>\n\n"
        f"• Title: <b>{title_display}</b>{desc_display}\n"
        f"• Total Quizzes: <b>{total}</b>\n"
        f"• Destination: <b>{dest}</b>\n"
        f"• Header Banner: <b>{banner}</b>\n"
        f"• Anonymous: <b>{anon}</b>\n"
        f"• Explanation: <b>{expl}</b>\n"
        f"• Time Limit: <b>{timer}</b>\n\n"
        "Are you sure you want to proceed?"
    )


async def confirm_publish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the final confirmation prompt before publishing."""
    query = update.callback_query
    if query:
        await query.answer()

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    if not questions:
        if query and query.message:
            await query.edit_message_text("⚠️ No active quiz batch found.")
        return ConversationHandler.END

    # Validation check: Ensure all questions are valid before publishing
    val_res = validator.validate_batch(questions)
    if not val_res.is_valid:
        first_err_idx = val_res.failed_question_indices[0] if val_res.failed_question_indices else 1
        if query:
            await query.answer("⚠️ Some questions need attention before publishing!", show_alert=True)

        report = val_res.get_formatted_error_report(
            max_display=3,
            custom_footer="💡 <i>Please fix the issues or drop the invalid questions before publishing:</i>",
        )
        warn_text = (
            f"❌ <b>Cannot Publish: {val_res.error_count} Issue(s) Found</b>\n\n"
            f"{report}"
        )
        keyboard = get_validation_error_keyboard(
            first_invalid_idx=first_err_idx,
            total_valid=val_res.valid_count,
            total_invalid=len(val_res.failed_question_indices),
            has_oversized=val_res.has_oversized_options,
        )
        if query and query.message:
            await query.edit_message_text(warn_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return QuizCreationState.PREVIEWING

    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()

    text = format_confirmation_overview(len(questions), settings)
    keyboard = get_publish_confirm_keyboard(len(questions))

    if query and query.message:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    return QuizCreationState.CONFIRMING_PUBLISH


async def start_publishing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute bulk publishing to Telegram with real-time progress updates."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer("Starting quiz publication...")

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()

    if not questions:
        await query.edit_message_text("⚠️ No questions found to publish.")
        return ConversationHandler.END

    bot = context.bot
    target_chat = settings.channel_id if settings.channel_id else query.message.chat_id

    # Initial Progress Message
    progress_msg = await query.edit_message_text(
        f"🚀 <b>Publishing quizzes to {target_chat}...</b>\n\n"
        "░░░░░░░░░░░░░░░\n\n"
        f"<b>0 / {len(questions)}</b> processed",
        parse_mode=ParseMode.HTML,
    )

    # Throttled progress update helper
    last_update_count = 0

    async def update_progress(completed: int, total: int, status_body: str):
        nonlocal last_update_count
        # Update UI every 2 questions or at the final question to avoid hitting Telegram message edit rate limits
        if completed - last_update_count >= 2 or completed == total:
            last_update_count = completed
            try:
                await progress_msg.edit_text(
                    f"🚀 <b>Publishing quizzes to {target_chat}...</b>\n\n{status_body}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.debug("Minor rate limit or identical text on progress edit: %s", e)

    # Optional: Persist or link in database
    db_job_id = None
    try:
        with SessionLocal() as db:
            saved_id = context.user_data.get("saved_quiz_set_id")
            quiz_set = get_quiz_set_by_id(db, quiz_set_id=saved_id) if saved_id else None

            if not quiz_set:
                user = get_or_create_user(
                    db,
                    telegram_id=update.effective_user.id,
                    username=update.effective_user.username,
                )
                quiz_set = save_quiz_batch(
                    db,
                    user_id=user.id,
                    questions=questions,
                    settings=settings,
                    title=settings.title if settings.title else f"Batch of {len(questions)} Questions",
                    description=settings.description,
                )
            job = create_publish_job(db, quiz_set_id=quiz_set.id, total=len(questions))
            db_job_id = job.id
    except Exception as db_err:
        logger.warning("Could not persist quiz batch to database (proceeding anyway): %s", db_err)

    # Perform publishing
    summary: PublishingSummary = await publishing_service.publish_batch(
        bot=bot,
        target_chat_id=target_chat,
        questions=questions,
        settings=settings,
        progress_callback=update_progress,
    )

    # Update database publish job
    if db_job_id:
        try:
            with SessionLocal() as db:
                complete_publish_job(
                    db,
                    job_id=db_job_id,
                    successful=summary.successful,
                    failed=summary.failed,
                    status="completed" if summary.is_complete_success else "partial_failure",
                )
        except Exception as db_err:
            logger.warning("Could not update database publish job: %s", db_err)

    # Clean up or prepare retry
    if summary.is_complete_success:
        # All published successfully!
        context.user_data.pop("bulk_questions", None)
        context.user_data.pop("failed_questions", None)

        success_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📦 Create Another Batch", callback_data="action_bulk_create")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu")],
            ]
        )
        await progress_msg.edit_text(
            f"🎉 <b>Publishing Complete!</b>\n\n"
            f"✅ Successfully created: <b>{summary.successful}</b> quiz poll(s)\n"
            f"❌ Failed: <b>0</b>\n"
            f"📢 Destination: <b>{target_chat}</b>\n\n"
            "All quiz polls have been posted natively!",
            reply_markup=success_kb,
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END
    else:
        # Some or all failed
        failed_questions = [item.question for item in summary.failed_items]
        context.user_data["bulk_questions"] = failed_questions
        context.user_data["failed_questions"] = summary.failed_items

        err_details = "\n".join(
            [f"• Q{item.index}: {item.error_message}" for item in summary.failed_items[:3]]
        )

        await progress_msg.edit_text(
            f"⚠️ <b>Publishing Finished with Issues</b>\n\n"
            f"✅ Successfully created: <b>{summary.successful}</b>\n"
            f"❌ Failed to post: <b>{summary.failed}</b>\n\n"
            f"<b>Errors:</b>\n{err_details}\n\n"
            "You can retry posting the failed questions directly:",
            reply_markup=get_retry_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return QuizCreationState.CONFIRMING_PUBLISH


async def retry_failed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Retry publishing failed questions."""
    query = update.callback_query
    if query:
        await query.answer("Retrying failed questions...")
    return await start_publishing_callback(update, context)
