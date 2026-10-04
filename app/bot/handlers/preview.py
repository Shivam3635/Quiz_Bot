"""Paginated preview and interactive question editing handlers."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.keyboards.preview import (
    get_delete_confirm_keyboard,
    get_edit_question_keyboard,
    get_preview_keyboard,
)
from app.bot.states import QuizCreationState
from app.parser.models import QuizQuestion, QuizSettings
from app.parser.parser import BulkQuizParser
from app.parser.validator import (
    QuizValidator,
    TELEGRAM_MAX_EXPLANATION_LENGTH,
    TELEGRAM_MAX_OPTION_LENGTH,
    TELEGRAM_MAX_QUESTION_LENGTH,
)
from app.database.database import SessionLocal
from app.database.repositories import (
    get_quiz_set_by_id,
    reconstruct_quiz_data,
    update_quiz_set_questions,
)
from app.utils.logger import setup_logger

logger = setup_logger(__name__)
parser = BulkQuizParser()
validator = QuizValidator()


def format_question_preview(q: QuizQuestion, index: int, total: int, settings: QuizSettings) -> str:
    """Format single question preview with choices, correct answer, and validation warnings if any."""
    val_errors = validator.validate_single(q, index + 1)
    status_tag = " ⚠️ <b>(NEEDS ATTENTION)</b>" if val_errors else ""

    lines = [
        f"👀 <b>Quiz Preview ({index + 1} of {total})</b>{status_tag}\n",
    ]

    if val_errors:
        lines.append("⚠️ <b>Issues detected on this question:</b>")
        for err in val_errors:
            lines.append(f"• <i>{err.message}</i>")
        lines.append("")

    lines.append(f"❓ <b>{q.display_question}</b>\n")

    for opt_idx, opt_text in enumerate(q.options):
        letter = chr(ord("A") + opt_idx)
        opt_len = len(opt_text.strip())
        len_warning = f" ⚠️ <b>({opt_len} chars / limit 100)</b>" if opt_len > 100 else ""
        if opt_idx == q.correct_option:
            lines.append(f"<b>[{letter}] {opt_text}</b>  ✅ <i>(Correct Answer)</i>{len_warning}")
        else:
            lines.append(f"[{letter}] {opt_text}{len_warning}")

    if q.explanation and settings.explanation_enabled:
        lines.append(f"\n💡 <b>Explanation:</b> {q.explanation}")

    return "\n".join(lines)


def format_copyable_question(q: QuizQuestion, index: int) -> str:
    """Format question in pasteable format for easy 1-tap clipboard copying."""
    lines = [f"{q.display_question}"]
    for opt_idx, opt in enumerate(q.options):
        letter = chr(ord("A") + opt_idx)
        lines.append(f"{letter}. {opt}")
    lines.append(f"Answer: {q.correct_letter}")
    if q.explanation:
        lines.append(f"Explanation: {q.explanation}")
    return "\n".join(lines)


async def preview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Display the first question or navigate through questions."""
    query = update.callback_query
    if query:
        await query.answer()

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    if not questions:
        if query and query.message:
            await query.edit_message_text("⚠️ No active quiz batch found. Use /start to begin.")
        return ConversationHandler.END

    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()

    # Determine index from callback data or session
    current_index = context.user_data.get("preview_index", 0)
    if query and query.data and query.data.startswith("preview_nav_"):
        try:
            current_index = int(query.data.split("_")[-1])
        except ValueError:
            current_index = 0

    current_index = max(0, min(current_index, len(questions) - 1))
    context.user_data["preview_index"] = current_index
    context.user_data.pop("edit_mode", None)

    q = questions[current_index]
    val_res = validator.validate_batch(questions)
    invalid_0_based = [idx - 1 for idx in val_res.failed_question_indices]

    saved_id = context.user_data.get("saved_quiz_set_id")
    text = format_question_preview(q, current_index, len(questions), settings)
    keyboard = get_preview_keyboard(
        current_index, len(questions), invalid_indices=invalid_0_based, saved_quiz_set_id=saved_id
    )

    if query and query.message:
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.warning("Error editing preview message: %s", e)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    return QuizCreationState.PREVIEWING


async def preview_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle click on passive pagination indicator button."""
    query = update.callback_query
    if query:
        await query.answer()
    return QuizCreationState.PREVIEWING


async def edit_current_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Open the editor menu for the current question."""
    query = update.callback_query
    if query:
        await query.answer()

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    current_index = context.user_data.get("preview_index", 0)

    if not questions or current_index >= len(questions):
        return await preview_callback(update, context)

    q = questions[current_index]
    val_errors = validator.validate_single(q, current_index + 1)
    status_tag = " ⚠️ <b>(NEEDS ATTENTION)</b>" if val_errors else ""

    err_section = ""
    if val_errors:
        err_bullets = "\n".join([f"• <b>{e.message}</b>" for e in val_errors])
        err_section = f"⚠️ <b>Issues detected on this question:</b>\n{err_bullets}\n\n"

    options_summary = "\n".join(
        [
            f"[{chr(ord('A') + i)}] {opt} {'✅' if i == q.correct_option else ''}{' ⚠️(' + str(len(opt.strip())) + ' chars / limit 100)' if len(opt.strip()) > 100 else ''}"
            for i, opt in enumerate(q.options)
        ]
    )

    text = (
        f"✏️ <b>Editing Question {current_index + 1} of {len(questions)}</b>{status_tag}\n\n"
        f"{err_section}"
        f"<b>Question:</b> {q.display_question}\n\n"
        f"<b>Options:</b>\n{options_summary}\n\n"
        f"<b>Explanation:</b> {q.explanation or 'None'}\n\n"
        "💡 <i>Tap an option button below to edit its text, or tap a letter to switch the correct answer:</i>"
    )

    saved_id = context.user_data.get("saved_quiz_set_id")
    keyboard = get_edit_question_keyboard(q, current_index, saved_quiz_set_id=saved_id)

    if query and query.message:
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.warning("Error editing edit question screen: %s", e)

    return QuizCreationState.EDITING_QUESTION


async def set_correct_answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Change the correct answer of the currently edited question."""
    query = update.callback_query
    if not query or not query.data:
        return QuizCreationState.EDITING_QUESTION

    try:
        new_ans_idx = int(query.data.split("_")[-1])
    except ValueError:
        return QuizCreationState.EDITING_QUESTION

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    current_index = context.user_data.get("preview_index", 0)

    if questions and 0 <= current_index < len(questions):
        q = questions[current_index]
        if 0 <= new_ans_idx < len(q.options):
            q.correct_option = new_ans_idx
            letter = chr(ord("A") + new_ans_idx)
            await query.answer(f"✅ Correct answer set to Option {letter}!")

            # Single source of truth: sync directly to DB if quiz is persisted
            saved_id = context.user_data.get("saved_quiz_set_id")
            if saved_id:
                try:
                    with SessionLocal() as db:
                        update_quiz_set_questions(db, saved_id, questions)
                except Exception as e:
                    logger.error("DB sync failed on correct answer change: %s", e)

    return await edit_current_question_callback(update, context)


async def prompt_edit_question_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to edit question text with copyable snippet."""
    query = update.callback_query
    if query:
        await query.answer()

    context.user_data["edit_mode"] = "question_text"
    current_index = context.user_data.get("preview_index", 0)
    questions = context.user_data.get("bulk_questions") or []
    curr_q = questions[current_index].display_question if questions else ""

    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Cancel", callback_data="edit_current_question")]]
    )

    if query and query.message:
        await query.edit_message_text(
            f"✏️ <b>Edit Question Text (Q{current_index + 1})</b>\n\n"
            f"Current question text <i>(tap to copy)</i>:\n"
            f"<code>{curr_q}</code>\n\n"
            "Send your revised question text below:",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML,
        )

    return QuizCreationState.EDITING_QUESTION


async def prompt_edit_option_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to edit a specific option's text."""
    query = update.callback_query
    if not query or not query.data:
        return QuizCreationState.EDITING_QUESTION

    await query.answer()

    try:
        opt_idx = int(query.data.split("_")[-1])
    except ValueError:
        return QuizCreationState.EDITING_QUESTION

    current_index = context.user_data.get("preview_index", 0)
    questions = context.user_data.get("bulk_questions") or []

    if not questions or current_index >= len(questions):
        return await preview_callback(update, context)

    q = questions[current_index]
    if opt_idx >= len(q.options):
        return await edit_current_question_callback(update, context)

    letter = chr(ord("A") + opt_idx)
    current_opt_text = q.options[opt_idx]

    context.user_data["edit_mode"] = f"opt_{opt_idx}"

    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Cancel", callback_data="edit_current_question")]]
    )

    if query.message:
        await query.edit_message_text(
            f"✏️ <b>Edit Option {letter} (Question {current_index + 1})</b>\n\n"
            f"Current text <i>(tap to copy)</i>:\n"
            f"<code>{current_opt_text}</code>\n\n"
            f"Send the new text for Option {letter} below:",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML,
        )

    return QuizCreationState.EDITING_QUESTION


async def prompt_edit_explanation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type new explanation."""
    query = update.callback_query
    if query:
        await query.answer()

    context.user_data["edit_mode"] = "explanation"
    current_index = context.user_data.get("preview_index", 0)
    questions = context.user_data.get("bulk_questions") or []
    curr_expl = questions[current_index].explanation if questions else None

    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Cancel", callback_data="edit_current_question")]]
    )

    expl_display = f"<code>{curr_expl}</code>" if curr_expl else "<i>None</i>"

    if query and query.message:
        await query.edit_message_text(
            f"💡 <b>Edit Explanation (Question {current_index + 1})</b>\n\n"
            f"Current explanation:\n{expl_display}\n\n"
            "Send the new explanation text below.\n"
            "<i>(Or send <code>none</code> to remove it)</i>",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML,
        )

    return QuizCreationState.EDITING_QUESTION


async def show_copyable_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Display the entire question in <code> tags for 1-tap clipboard copying."""
    query = update.callback_query
    if query:
        await query.answer("Tap the text block to copy!")

    current_index = context.user_data.get("preview_index", 0)
    questions = context.user_data.get("bulk_questions") or []

    if not questions or current_index >= len(questions):
        return await preview_callback(update, context)

    q = questions[current_index]
    formatted = format_copyable_question(q, current_index)

    back_kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 Back to Edit Menu", callback_data="edit_current_question")],
            [InlineKeyboardButton("👀 Back to Preview", callback_data="goto_preview")],
        ]
    )

    if query and query.message:
        await query.edit_message_text(
            f"📋 <b>Question {current_index + 1} (Tap below to copy to clipboard)</b>:\n\n"
            f"<code>{formatted}</code>\n\n"
            "<i>You can tap to copy, paste into your input bar, edit with cursor, and send!</i>",
            reply_markup=back_kb,
            parse_mode=ParseMode.HTML,
        )

    return QuizCreationState.EDITING_QUESTION


async def delete_question_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask confirmation before deleting question."""
    query = update.callback_query
    if query:
        await query.answer()

    current_index = context.user_data.get("preview_index", 0)
    questions = context.user_data.get("bulk_questions") or []

    if query and query.message and questions:
        q = questions[current_index]
        disp_q = q.display_question
        snippet = disp_q[:60] + "..." if len(disp_q) > 60 else disp_q
        await query.edit_message_text(
            f"🗑️ <b>Delete Question {current_index + 1}?</b>\n\n"
            f"<i>\"{snippet}\"</i>\n\n"
            "Are you sure you want to remove this question from the batch?",
            reply_markup=get_delete_confirm_keyboard(current_index),
            parse_mode=ParseMode.HTML,
        )

    return QuizCreationState.PREVIEWING


async def confirm_delete_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Delete the selected question from the batch."""
    query = update.callback_query
    if query:
        await query.answer("Question deleted.")

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    current_index = context.user_data.get("preview_index", 0)

    if questions and 0 <= current_index < len(questions):
        questions.pop(current_index)
        context.user_data["bulk_questions"] = questions

        # Single source of truth: sync directly to DB if quiz is persisted
        saved_id = context.user_data.get("saved_quiz_set_id")
        if saved_id:
            try:
                with SessionLocal() as db:
                    update_quiz_set_questions(db, saved_id, questions)
            except Exception as e:
                logger.error("DB sync failed on question delete: %s", e)

    if not questions:
        if query and query.message:
            from app.bot.keyboards.main import get_main_menu_keyboard
            await query.edit_message_text(
                "🗑️ <b>All questions in this batch have been deleted.</b>\n\n"
                "You can start a new batch from the main menu:",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        return ConversationHandler.END

    new_index = max(0, min(current_index, len(questions) - 1))
    context.user_data["preview_index"] = new_index

    return await preview_callback(update, context)


async def receive_question_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process text input when user is in edit mode."""
    if not update.message or not update.message.text:
        return QuizCreationState.EDITING_QUESTION

    text = update.message.text.strip()
    edit_mode = context.user_data.get("edit_mode")
    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    current_index = context.user_data.get("preview_index", 0)

    if not questions or current_index >= len(questions):
        return await preview_callback(update, context)

    q = questions[current_index]

    # Case 1: Editing Question text
    if edit_mode == "question_text":
        if len(text) > TELEGRAM_MAX_QUESTION_LENGTH:
            await update.message.reply_text(
                f"⚠️ Question text is too long ({len(text)} chars). Limit is {TELEGRAM_MAX_QUESTION_LENGTH} characters. Please send a shorter text:"
            )
            return QuizCreationState.EDITING_QUESTION

        q.question = text
        context.user_data.pop("edit_mode", None)
        rem_errs = validator.validate_single(q, current_index + 1)
        if not rem_errs:
            await update.message.reply_text(
                f"✅ <b>Question text updated! Question {current_index + 1} has no remaining issues.</b>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text("✅ <b>Question text updated!</b>", parse_mode=ParseMode.HTML)

        # Single source of truth: sync directly to DB if quiz is persisted
        saved_id = context.user_data.get("saved_quiz_set_id")
        if saved_id:
            try:
                with SessionLocal() as db:
                    update_quiz_set_questions(db, saved_id, questions)
            except Exception as e:
                logger.error("DB sync failed on question text edit: %s", e)

        return await preview_callback(update, context)

    # Case 2: Editing a specific Option
    elif edit_mode and edit_mode.startswith("opt_"):
        try:
            opt_idx = int(edit_mode.split("_")[-1])
        except ValueError:
            return await preview_callback(update, context)

        if len(text) > TELEGRAM_MAX_OPTION_LENGTH:
            await update.message.reply_text(
                f"⚠️ Option text is too long ({len(text)} chars). Limit is {TELEGRAM_MAX_OPTION_LENGTH}. Please send a shorter text:"
            )
            return QuizCreationState.EDITING_QUESTION

        if 0 <= opt_idx < len(q.options):
            q.options[opt_idx] = text
            letter = chr(ord("A") + opt_idx)
            context.user_data.pop("edit_mode", None)
            rem_errs = validator.validate_single(q, current_index + 1)
            if not rem_errs:
                await update.message.reply_text(
                    f"✅ <b>Option {letter} updated! Question {current_index + 1} has no remaining issues.</b>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_text(f"✅ <b>Option {letter} updated!</b>", parse_mode=ParseMode.HTML)

            # Single source of truth: sync directly to DB if quiz is persisted
            saved_id = context.user_data.get("saved_quiz_set_id")
            if saved_id:
                try:
                    with SessionLocal() as db:
                        update_quiz_set_questions(db, saved_id, questions)
                except Exception as e:
                    logger.error("DB sync failed on option edit: %s", e)

        return await preview_callback(update, context)

    # Case 3: Editing Explanation
    elif edit_mode == "explanation":
        if text.lower() in ("none", "remove", "clear", "-"):
            q.explanation = None
            await update.message.reply_text("✅ <b>Explanation removed!</b>", parse_mode=ParseMode.HTML)
        else:
            if len(text) > TELEGRAM_MAX_EXPLANATION_LENGTH:
                await update.message.reply_text(
                    f"⚠️ Explanation is too long ({len(text)} chars). Limit is {TELEGRAM_MAX_EXPLANATION_LENGTH}. Please send a shorter explanation:"
                )
                return QuizCreationState.EDITING_QUESTION
            q.explanation = text
            rem_errs = validator.validate_single(q, current_index + 1)
            if not rem_errs:
                await update.message.reply_text(
                    f"✅ <b>Explanation updated! Question {current_index + 1} has no remaining issues.</b>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_text("✅ <b>Explanation updated!</b>", parse_mode=ParseMode.HTML)

        context.user_data.pop("edit_mode", None)

        # Single source of truth: sync directly to DB if quiz is persisted
        saved_id = context.user_data.get("saved_quiz_set_id")
        if saved_id:
            try:
                with SessionLocal() as db:
                    update_quiz_set_questions(db, saved_id, questions)
            except Exception as e:
                logger.error("DB sync failed on option/explanation edit: %s", e)

        return await preview_callback(update, context)

    # Fallback: User sent a replacement question paste
    parsed = parser.parse(text)
    if not parsed.has_errors and len(parsed.questions) > 0:
        questions[current_index] = parsed.questions[0]
        context.user_data["bulk_questions"] = questions
        context.user_data.pop("edit_mode", None)

        saved_id = context.user_data.get("saved_quiz_set_id")
        if saved_id:
            try:
                with SessionLocal() as db:
                    update_quiz_set_questions(db, saved_id, questions)
            except Exception as e:
                logger.error("DB sync failed on replacement question paste: %s", e)

        rem_errs = validator.validate_single(questions[current_index], current_index + 1)
        if not rem_errs:
            await update.message.reply_text(
                f"✅ <b>Question {current_index + 1} replaced successfully! All issues resolved.</b>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text("✅ <b>Question replaced successfully!</b>", parse_mode=ParseMode.HTML)
        return await preview_callback(update, context)

    return await preview_callback(update, context)


async def jump_next_invalid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Jump to the next question with validation errors in preview."""
    query = update.callback_query
    if query:
        await query.answer()

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    current_index = context.user_data.get("preview_index", 0)
    val_res = validator.validate_batch(questions)

    failed_0_based = [idx - 1 for idx in val_res.failed_question_indices]
    if not failed_0_based:
        if query:
            await query.answer("🎉 All questions are now valid!")
        return await preview_callback(update, context)

    # Find the next failed index after current_index (or cycle to first)
    next_idx = next((i for i in failed_0_based if i > current_index), failed_0_based[0])
    context.user_data["preview_index"] = next_idx
    return await preview_callback(update, context)


async def fix_invalid_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Jump directly into editing the selected question."""
    query = update.callback_query
    if query and query.data:
        await query.answer()
        try:
            target_idx = int(query.data.split("_")[-1])
            context.user_data["preview_index"] = target_idx
        except ValueError:
            pass
    return await edit_current_question_callback(update, context)


async def drop_invalid_questions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Drop questions that have validation errors and continue with the valid ones."""
    query = update.callback_query
    if query:
        await query.answer()

    questions: list[QuizQuestion] = context.user_data.get("bulk_questions") or []
    val_res = validator.validate_batch(questions)

    if not val_res.valid_questions:
        if query and query.message:
            await query.edit_message_text(
                "⚠️ <b>No valid questions to keep.</b>\n\n"
                "Please fix the questions or start a new quiz batch with /newquiz.",
                parse_mode=ParseMode.HTML,
            )
        return QuizCreationState.WAITING_FOR_BULK_INPUT

    dropped_count = len(questions) - len(val_res.valid_questions)
    context.user_data["bulk_questions"] = val_res.valid_questions
    context.user_data["preview_index"] = 0

    saved_id = context.user_data.get("saved_quiz_set_id")
    if saved_id:
        try:
            with SessionLocal() as db:
                update_quiz_set_questions(db, saved_id, val_res.valid_questions)
        except Exception as e:
            logger.error("DB sync failed on drop invalid: %s", e)

    success_text = (
        f"🗑️ <b>Dropped {dropped_count} invalid question(s).</b>\n\n"
        f"✅ <b>{len(val_res.valid_questions)} valid question(s) retained!</b>\n\n"
        "You can now configure settings, preview, or publish directly:"
    )

    action_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚙️ Configure Settings", callback_data="goto_settings"),
                InlineKeyboardButton("👀 Preview Quizzes", callback_data="goto_preview"),
            ],
            [
                InlineKeyboardButton("🚀 Publish Directly", callback_data="goto_confirm_publish"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="action_cancel"),
            ],
        ]
    )

    if query and query.message:
        await query.edit_message_text(success_text, reply_markup=action_keyboard, parse_mode=ParseMode.HTML)

    return QuizCreationState.CONFIGURING_SETTINGS


async def edit_saved_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point from saved quiz preview to begin editing a specific question in-place."""
    query = update.callback_query
    if query:
        await query.answer()

    if not query or not query.data:
        return QuizCreationState.PREVIEWING

    parts = query.data.split("_")
    # "edit_saved_q_<quiz_set_id>_<idx>"
    try:
        quiz_set_id = int(parts[3])
        idx = int(parts[4])
    except (IndexError, ValueError):
        return QuizCreationState.PREVIEWING

    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
        if not quiz_set or not quiz_set.questions:
            if query and query.message:
                await query.edit_message_text(
                    "⚠️ Quiz questions could not be loaded.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Back to Quiz Details", callback_data=f"view_quiz_{quiz_set_id}")]]
                    ),
                )
            return ConversationHandler.END

        questions, settings = reconstruct_quiz_data(quiz_set)

    context.user_data["bulk_questions"] = questions
    context.user_data["quiz_settings"] = settings
    context.user_data["saved_quiz_set_id"] = quiz_set_id
    context.user_data["preview_index"] = max(0, min(idx, len(questions) - 1))

    return await edit_current_question_callback(update, context)

