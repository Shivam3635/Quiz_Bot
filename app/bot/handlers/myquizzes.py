"""Handlers for /myquizzes dashboard, review, and 1-click re-publishing."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.keyboards.myquizzes import (
    get_delete_confirm_keyboard,
    get_my_quizzes_keyboard,
    get_preview_saved_quiz_keyboard,
    get_quiz_details_keyboard,
    get_republish_confirm_keyboard,
)
from app.database.database import SessionLocal
from app.database.repositories import (
    create_publish_job,
    complete_publish_job,
    delete_quiz_set,
    get_quiz_set_by_id,
    get_user_quiz_sets,
    reconstruct_quiz_data,
    update_quiz_set_questions,
)
from app.services.publishing_service import PublishingService, PublishingSummary
from app.services.telegram_service import TelegramService
from app.utils.logger import setup_logger

logger = setup_logger(__name__)
publishing_service = PublishingService()
telegram_service = TelegramService()

PER_PAGE = 5


async def my_quizzes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for /myquizzes dashboard displaying user's saved quizzes."""
    user = update.effective_user
    user_id = user.id if user else 0

    query = update.callback_query
    if query:
        await query.answer()

    with SessionLocal() as db:
        quiz_sets, total_count = get_user_quiz_sets(db, telegram_id=user_id, limit=PER_PAGE, offset=0)
        keyboard = get_my_quizzes_keyboard(quiz_sets, page=0, total_count=total_count, per_page=PER_PAGE) if total_count > 0 else None

    if total_count == 0:
        empty_text = (
            "📚 <b>My Quizzes Dashboard</b>\n\n"
            "<i>You don't have any saved quiz sets yet!</i>\n\n"
            "Create your first batch of quizzes using /newquiz or the button below:"
        )
        empty_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Create a Quiz", callback_data="action_bulk_create")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu")],
        ])
        if query and query.message:
            await query.edit_message_text(empty_text, reply_markup=empty_kb, parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text(empty_text, reply_markup=empty_kb, parse_mode=ParseMode.HTML)
        return

    text = (
        "📚 <b>My Quizzes Dashboard</b>\n\n"
        f"You have <b>{total_count}</b> saved quiz set(s).\n"
        "Tap on any quiz below to inspect details, preview questions, or <b>re-publish in 1 click</b>:\n"
    )

    if query and query.message:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def my_quizzes_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pagination across user's saved quiz sets."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    page = int(query.data.split("_")[-1])
    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_sets, total_count = get_user_quiz_sets(
            db, telegram_id=user_id, limit=PER_PAGE, offset=page * PER_PAGE
        )
        keyboard = get_my_quizzes_keyboard(quiz_sets, page=page, total_count=total_count, per_page=PER_PAGE)

    text = (
        "📚 <b>My Quizzes Dashboard</b>\n\n"
        f"You have <b>{total_count}</b> saved quiz set(s).\n"
        "Tap on any quiz below to inspect details, preview questions, or <b>re-publish in 1 click</b>:\n"
    )

    if query.message:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def myquizzes_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """No-op callback for non-interactive indicators."""
    query = update.callback_query
    if query:
        await query.answer()


async def view_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display comprehensive details and management controls for a saved quiz set."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    quiz_set_id = int(query.data.split("_")[-1])
    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
        if not quiz_set:
            if query.message:
                await query.edit_message_text(
                    "⚠️ Quiz set not found or belongs to another user.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to My Quizzes", callback_data="my_quizzes_list_0")]]),
                )
            return

        title = quiz_set.title
        desc = quiz_set.description or "None"
        created_str = quiz_set.created_at.strftime("%Y-%m-%d %H:%M UTC") if quiz_set.created_at else "Unknown"
        q_count = len(quiz_set.questions)
        rec = quiz_set.settings
        timer_str = f"{rec.time_limit}s" if rec and rec.time_limit else "No limit"
        mode_str = "Anonymous" if not rec or rec.is_anonymous else "Public"
        expl_str = "Enabled" if not rec or rec.explanation_enabled else "Disabled"

        # Check for custom target channel overridden in session
        custom_dest = context.user_data.get(f"custom_dest_{quiz_set_id}")
        target_str = custom_dest if custom_dest else (rec.channel_id if rec and rec.channel_id else "This chat (Default)")

        # Publish history
        jobs_count = len(quiz_set.publish_jobs)
        last_job = quiz_set.publish_jobs[-1] if jobs_count > 0 else None
        last_published = (
            last_job.completed_at.strftime("%Y-%m-%d %H:%M UTC")
            if last_job and last_job.completed_at
            else ("In progress" if last_job else "Never")
        )

    details_text = (
        f"📊 <b>Quiz Details: {title}</b>\n\n"
        f"• <b>Description:</b> <i>{desc}</i>\n"
        f"• <b>Total Questions:</b> <code>{q_count}</code>\n"
        f"• <b>Created:</b> <code>{created_str}</code>\n"
        f"• <b>Last Published:</b> <code>{last_published}</code>\n"
        f"• <b>Target Destination:</b> <b>{target_str}</b>\n"
        f"• <b>Timer:</b> <code>{timer_str}</code>\n"
        f"• <b>Mode:</b> <code>{mode_str}</code>\n"
        f"• <b>Explanations:</b> <code>{expl_str}</code>\n\n"
        "<i>Select an action below:</i>"
    )

    if query.message:
        bot_user = context.bot.username if context.bot else None
        await query.edit_message_text(
            details_text,
            reply_markup=get_quiz_details_keyboard(quiz_set_id, bot_username=bot_user),
            parse_mode=ParseMode.HTML,
        )

    # Clean up creation session if coming from creation flow
    if context.user_data.get("saved_quiz_set_id") or context.user_data.get("quiz_settings"):
        from app.bot.handlers.bulk import clear_creation_session
        clear_creation_session(context, user_id, update.effective_chat.id if update.effective_chat else 0)

    return ConversationHandler.END


async def preview_saved_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Preview questions in a saved quiz set one-by-one."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    parts = query.data.split("_")
    quiz_set_id = int(parts[2])
    idx = int(parts[3])
    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
        if not quiz_set or not quiz_set.questions:
            if query.message:
                await query.edit_message_text(
                    "⚠️ Quiz questions could not be loaded.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="my_quizzes_list_0")]]),
                )
            return

        total_q = len(quiz_set.questions)
        idx = max(0, min(idx, total_q - 1))
        q = quiz_set.questions[idx]
        sorted_opts = sorted(q.options, key=lambda o: o.position)

        opt_lines = []
        for opt_idx, opt in enumerate(sorted_opts):
            letter = chr(ord("A") + opt_idx)
            is_correct = opt_idx == q.correct_option
            mark = " ✅" if is_correct else ""
            opt_lines.append(f"  {letter}) {opt.option_text}{mark}")

        expl_text = f"\n\n💡 <b>Explanation:</b> {q.explanation}" if q.explanation else ""

        from app.utils.helpers import format_bilingual_question_text
        display_q_text = format_bilingual_question_text(q.question)
        text = (
            f"📋 <b>Question {idx + 1} of {total_q}</b>\n\n"
            f"<b>{display_q_text}</b>\n\n"
            + "\n".join(opt_lines)
            + expl_text
        )

        keyboard = get_preview_saved_quiz_keyboard(quiz_set_id, idx, total_q)

        if query.message:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def delete_saved_question_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt confirmation before deleting a question from a saved quiz."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    parts = query.data.split("_")
    # "del_saved_q_<quiz_set_id>_<idx>"
    try:
        quiz_set_id = int(parts[3])
        idx = int(parts[4])
    except (IndexError, ValueError):
        return

    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
        if not quiz_set or not quiz_set.questions:
            return
        total_q = len(quiz_set.questions)
        idx = max(0, min(idx, total_q - 1))
        q = quiz_set.questions[idx]
        disp_q = q.question
        snippet = disp_q[:60] + "..." if len(disp_q) > 60 else disp_q

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ Yes, Delete Question", callback_data=f"confirm_del_saved_q_{quiz_set_id}_{idx}"),
        ],
        [
            InlineKeyboardButton("🔙 Cancel", callback_data=f"preview_saved_{quiz_set_id}_{idx}"),
        ],
    ])

    if query.message:
        await query.edit_message_text(
            f"🗑️ <b>Delete Question {idx + 1} of {total_q}?</b>\n\n"
            f"<i>\"{snippet}\"</i>\n\n"
            "Are you sure you want to permanently remove this question from your saved quiz?",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )


async def confirm_del_saved_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute deletion of a question from a saved quiz in the database."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer("Question deleted.")

    parts = query.data.split("_")
    # "confirm_del_saved_q_<quiz_set_id>_<idx>"
    try:
        quiz_set_id = int(parts[4])
        idx = int(parts[5])
    except (IndexError, ValueError):
        return

    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
        if not quiz_set:
            return
        questions, _ = reconstruct_quiz_data(quiz_set)
        if 0 <= idx < len(questions):
            questions.pop(idx)
            update_quiz_set_questions(db, quiz_set_id, questions)

    new_total = len(questions)
    if new_total == 0:
        query.data = f"view_quiz_{quiz_set_id}"
        return await view_quiz_callback(update, context)

    new_idx = min(idx, new_total - 1)
    query.data = f"preview_saved_{quiz_set_id}_{new_idx}"
    return await preview_saved_quiz_callback(update, context)


async def republish_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt confirmation overview before 1-click re-publishing."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    quiz_set_id = int(query.data.split("_")[-1])
    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
        if not quiz_set:
            return

        title = quiz_set.title
        total_q = len(quiz_set.questions)
        rec = quiz_set.settings
        custom_dest = context.user_data.get(f"custom_dest_{quiz_set_id}")
        target = custom_dest if custom_dest else (rec.channel_id if rec and rec.channel_id else "This chat (Current conversation)")

    text = (
        "🚀 <b>Ready to Publish!</b>\n\n"
        f"• Quiz: <b>{title}</b>\n"
        f"• Total Questions: <b>{total_q}</b>\n"
        f"• Target Destination: <b>{target}</b>\n"
        "• Timer: <b>No limit (Off)</b>\n"
        "• Channel Header Banner: <b>ON ✅</b>\n\n"
        "Tap <b>Publish Now</b> to send polls directly without re-entering any questions:"
    )

    if query.message:
        await query.edit_message_text(
            text,
            reply_markup=get_republish_confirm_keyboard(quiz_set_id),
            parse_mode=ParseMode.HTML,
        )


async def republish_change_dest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to supply a new target channel username for re-publishing."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    quiz_set_id = int(query.data.split("_")[-1])
    context.user_data["waiting_for_republish_dest_for"] = quiz_set_id

    cancel_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Quiz", callback_data=f"view_quiz_{quiz_set_id}")],
    ])

    text = (
        "📢 <b>Change Target Destination</b>\n\n"
        "Send the <b>@username</b> of your target channel or group (e.g. <code>@myquizchannel</code>).\n"
        "<i>(Or send <code>this</code> or <code>me</code> to publish here in this conversation)</i>\n\n"
        "⚠️ <b>Important:</b> Ensure QuizBotPro is added as an <b>Administrator</b> with 'Post Messages' permissions in that channel."
    )

    if query.message:
        await query.edit_message_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)


async def receive_republish_dest_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input when user is entering a new destination for re-publishing."""
    quiz_set_id = context.user_data.get("waiting_for_republish_dest_for")
    if not quiz_set_id or not update.message or not update.message.text:
        return False

    context.user_data.pop("waiting_for_republish_dest_for", None)
    dest_text = update.message.text.strip()

    if dest_text.lower() in ("this", "me", "chat", "here", "none"):
        context.user_data[f"custom_dest_{quiz_set_id}"] = None
        target_name = "This chat (Current conversation)"
    else:
        # Validate channel access
        bot = context.bot
        valid, msg, _ = await telegram_service.validate_channel_access(bot, dest_text)
        if not valid:
            retry_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Quiz", callback_data=f"view_quiz_{quiz_set_id}")],
            ])
            await update.message.reply_text(
                f"{msg}\n\nPlease check permissions or type another @channelusername:",
                reply_markup=retry_kb,
                parse_mode=ParseMode.HTML,
            )
            context.user_data["waiting_for_republish_dest_for"] = quiz_set_id
            return True
        context.user_data[f"custom_dest_{quiz_set_id}"] = dest_text
        target_name = dest_text

    confirm_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Publish Now", callback_data=f"execute_republish_{quiz_set_id}")],
        [InlineKeyboardButton("🔙 Back to Quiz", callback_data=f"view_quiz_{quiz_set_id}")],
    ])

    await update.message.reply_text(
        f"🎯 <b>Target destination updated to: {target_name}</b>\n\n"
        "You can now proceed to re-publish immediately:",
        reply_markup=confirm_kb,
        parse_mode=ParseMode.HTML,
    )
    return True


async def execute_republish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute 1-click re-publishing with live progress bar and database job tracking."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer("Initiating publication...")

    quiz_set_id = int(query.data.split("_")[-1])
    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
        if not quiz_set or not quiz_set.questions:
            if query.message:
                await query.edit_message_text("⚠️ Quiz set contains no questions.")
            return

        questions, settings = reconstruct_quiz_data(quiz_set)
        # Requirement 5: publish all questions of the quiz without timer in same or target group depending on choice
        settings.time_limit = None

    # Check for custom destination override
    custom_dest = context.user_data.get(f"custom_dest_{quiz_set_id}")
    if custom_dest is not None:
        settings.channel_id = custom_dest

    bot = context.bot
    target_chat = settings.channel_id if settings.channel_id else query.message.chat_id

    # Create job in database
    db_job_id = None
    try:
        with SessionLocal() as db:
            job = create_publish_job(db, quiz_set_id=quiz_set_id, total=len(questions))
            db_job_id = job.id
    except Exception as db_err:
        logger.warning("Could not persist publish job: %s", db_err)

    # Initial Progress Message
    progress_msg = await query.edit_message_text(
        f"🚀 <b>Publishing {len(questions)} quiz(zes) to {target_chat}...</b>\n\n"
        "░░░░░░░░░░░░░░░\n\n"
        f"<b>0 / {len(questions)}</b> processed",
        parse_mode=ParseMode.HTML,
    )

    last_update_count = 0

    async def update_progress(completed: int, total: int, status_body: str):
        nonlocal last_update_count
        if completed - last_update_count >= 2 or completed == total:
            last_update_count = completed
            try:
                await progress_msg.edit_text(
                    f"🚀 <b>Publishing to {target_chat}...</b>\n\n{status_body}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.debug("Progress edit rate limit: %s", e)

    # Publish batch
    summary: PublishingSummary = await publishing_service.publish_batch(
        bot=bot,
        target_chat_id=target_chat,
        questions=questions,
        settings=settings,
        progress_callback=update_progress,
    )

    # Update database job
    if db_job_id:
        try:
            with SessionLocal() as db:
                complete_publish_job(
                    db,
                    job_id=db_job_id,
                    successful=summary.successful,
                    failed=summary.failed,
                    status="completed" if summary.is_complete_success else "partially_failed",
                )
        except Exception as db_err:
            logger.warning("Could not update publish job: %s", db_err)

    # Final summary message
    final_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 View Quiz Details", callback_data=f"view_quiz_{quiz_set_id}")],
        [InlineKeyboardButton("📚 Back to My Quizzes", callback_data="my_quizzes_list_0")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu")],
    ])

    if summary.is_complete_success:
        res_text = (
            "🎉 <b>Publish Complete!</b>\n\n"
            f"• Quiz: <b>{settings.title or 'Untitled Quiz'}</b>\n"
            f"• Successfully published: <b>{summary.successful} / {summary.total}</b>\n"
            f"• Destination: <b>{target_chat}</b>\n\n"
            "All quiz polls are now active in the target chat!"
        )
    else:
        res_text = (
            "⚠️ <b>Publish Finished with Warnings</b>\n\n"
            f"• Successfully published: <b>{summary.successful} / {summary.total}</b>\n"
            f"• Failed: <b>{summary.failed}</b>\n"
            f"• Destination: <b>{target_chat}</b>"
        )

    await progress_msg.edit_text(res_text, reply_markup=final_kb, parse_mode=ParseMode.HTML)


async def delete_saved_quiz_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to confirm quiz deletion."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    quiz_set_id = int(query.data.split("_")[-1])

    text = (
        "⚠️ <b>Delete Quiz Set?</b>\n\n"
        "Are you sure you want to delete this saved quiz set from your database?\n"
        "This action cannot be undone."
    )
    if query.message:
        await query.edit_message_text(
            text,
            reply_markup=get_delete_confirm_keyboard(quiz_set_id),
            parse_mode=ParseMode.HTML,
        )


async def confirm_delete_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete quiz set from database and return to dashboard."""
    query = update.callback_query
    if not query or not query.data:
        return

    quiz_set_id = int(query.data.split("_")[-1])
    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        success = delete_quiz_set(db, quiz_set_id=quiz_set_id, telegram_id=user_id)

    if success:
        await query.answer("🗑️ Quiz set deleted.")
    else:
        await query.answer("⚠️ Could not delete quiz set.")

    # Return to dashboard list
    return await my_quizzes_command(update, context)
