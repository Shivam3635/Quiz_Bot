"""Confirmation, bulk publishing execution, and retry handlers."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.keyboards.preview import (
    get_publish_confirm_keyboard,
    get_retry_keyboard,
    get_uncertain_confirm_keyboard,
)
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
validator = QuizValidator(allow_extended_options=True)


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


def format_failed_summary_message(summary: PublishingSummary, target_chat: str | int) -> str:
    """Format user-facing summary report for publishing runs with issues."""
    banner_warning = (
        "⚠️ <i>Note: The introductory header banner could not be posted and was omitted.</i>\n\n"
        if summary.banner_failed
        else ""
    )

    err_details = "\n".join(
        [f"• Q{item.index}: {item.error_message}" for item in summary.failed_items[:3]]
    )
    if len(summary.failed_items) > 3:
        err_details += f"\n• ... and {len(summary.failed_items) - 3} more"

    if summary.has_uncertain_failures:
        def_count = len(summary.definitive_failed_items)
        unc_count = len(summary.uncertain_items)
        return (
            f"⚠️ <b>Publishing Finished with Issues</b>\n\n"
            f"{banner_warning}"
            f"✅ Successfully created: <b>{summary.successful}</b>\n"
            f"❌ Confirmed failed: <b>{def_count}</b>\n"
            f"⏳ Uncertain delivery (timeout): <b>{unc_count}</b>\n\n"
            f"<b>Errors:</b>\n{err_details}\n\n"
            "⚠️ <b>Important Notice:</b> Some questions may have been published even though Telegram did not return a response. "
            "Retrying these questions may create duplicate polls. Please check your channel before continuing."
        )
    else:
        return (
            f"⚠️ <b>Publishing Finished with Issues</b>\n\n"
            f"{banner_warning}"
            f"✅ Successfully created: <b>{summary.successful}</b>\n"
            f"❌ Failed to post: <b>{summary.failed}</b>\n\n"
            f"<b>Errors:</b>\n{err_details}\n\n"
            "You can retry posting the failed questions directly:"
        )


async def start_publishing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute bulk publishing to Telegram with real-time progress updates."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    # Idempotency lock: prevent duplicate concurrent publishing from rapid clicks
    if context.user_data.get("is_publishing"):
        try:
            await query.answer("⚠️ Publishing is already in progress. Please wait.", show_alert=True)
        except Exception:
            pass
        return QuizCreationState.CONFIRMING_PUBLISH

    context.user_data["is_publishing"] = True
    try:
        try:
            await query.answer("Starting quiz publication...")
        except Exception:
            pass

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
            from app.bot.handlers.bulk import clear_creation_session
            clear_creation_session(context, update.effective_user.id if update.effective_user else 0, update.effective_chat.id if update.effective_chat else 0)

            banner_note = (
                "\n\n⚠️ <i>Note: The introductory header banner could not be posted and was omitted.</i>"
                if summary.banner_failed
                else ""
            )

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
                f"All quiz polls have been posted natively!{banner_note}",
                reply_markup=success_kb,
                parse_mode=ParseMode.HTML,
            )
            return ConversationHandler.END
        else:
            # Some or all failed
            definitive = summary.definitive_failed_items
            uncertain = summary.uncertain_items

            context.user_data["last_publishing_summary"] = summary
            context.user_data["target_chat"] = target_chat
            context.user_data["bulk_questions"] = [item.question for item in summary.failed_items]
            context.user_data["failed_questions"] = summary.failed_items
            context.user_data["definitive_failed_questions"] = [item.question for item in definitive]
            context.user_data["uncertain_failed_questions"] = [item.question for item in uncertain]

            msg_text = format_failed_summary_message(summary, target_chat)
            keyboard = get_retry_keyboard(
                has_uncertain=summary.has_uncertain_failures,
                has_confirmed=bool(definitive),
                confirmed_count=len(definitive),
            )

            await progress_msg.edit_text(
                msg_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return QuizCreationState.CONFIRMING_PUBLISH
    finally:
        context.user_data.pop("is_publishing", None)


async def retry_failed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Retry publishing failed questions (default retry flow)."""
    return await start_publishing_callback(update, context)


async def retry_confirmed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Retry publishing only questions with confirmed rejections, excluding uncertain deliveries."""
    confirmed_questions = context.user_data.get("definitive_failed_questions") or []
    if confirmed_questions:
        context.user_data["bulk_questions"] = confirmed_questions
    return await start_publishing_callback(update, context)


async def retry_with_uncertain_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user with explicit duplicate warning before retrying uncertain questions."""
    query = update.callback_query
    if query:
        await query.answer()

    uncertain_questions = context.user_data.get("uncertain_failed_questions") or []
    total_uncertain = len(uncertain_questions)

    warn_text = (
        "⚠️ <b>Duplicate Poll Risk Confirmation</b>\n\n"
        f"There are <b>{total_uncertain}</b> question(s) whose delivery could not be confirmed because Telegram did not return a response before the timeout.\n\n"
        "If Telegram processed the original request, retrying will create <b>duplicate polls</b> in your channel.\n\n"
        "💡 <i>Please check your destination chat to verify whether these polls were already posted before proceeding.</i>\n\n"
        "Do you want to retry these questions now?"
    )
    keyboard = get_uncertain_confirm_keyboard()
    if query and query.message:
        await query.edit_message_text(warn_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return QuizCreationState.CONFIRMING_PUBLISH


async def confirm_retry_uncertain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute retry of all failed items after explicit confirmation by the user."""
    query = update.callback_query
    if query:
        await query.answer("Proceeding with retry...")
    all_failed = [item.question for item in context.user_data.get("failed_questions", [])]
    if all_failed:
        context.user_data["bulk_questions"] = all_failed
    return await start_publishing_callback(update, context)


async def back_to_failed_summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to the failed publishing summary screen from the warning confirmation prompt."""
    query = update.callback_query
    if query:
        await query.answer()

    summary: Optional[PublishingSummary] = context.user_data.get("last_publishing_summary")
    target_chat = context.user_data.get("target_chat", "channel")

    if summary and query and query.message:
        definitive = summary.definitive_failed_items
        msg_text = format_failed_summary_message(summary, target_chat)
        keyboard = get_retry_keyboard(
            has_uncertain=summary.has_uncertain_failures,
            has_confirmed=bool(definitive),
            confirmed_count=len(definitive),
        )
        await query.edit_message_text(msg_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return QuizCreationState.CONFIRMING_PUBLISH
