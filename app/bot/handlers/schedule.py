"""Handlers for Phase 1: Single Quiz Scheduling."""

import html
import re
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.keyboards.schedule import (
    get_automation_menu_keyboard,
    get_my_schedules_keyboard,
    get_schedule_confirm_keyboard,
    get_schedule_date_keyboard,
    get_schedule_details_keyboard,
    get_schedule_groups_keyboard,
    get_schedule_quizzes_keyboard,
    get_schedule_time_keyboard,
    get_schedule_timezone_keyboard,
)
from app.bot.states import QuizScheduleState
from app.database.database import SessionLocal
from app.database.models import GroupMember
from app.database.repositories import (
    cancel_quiz_schedule,
    create_quiz_schedule,
    get_or_create_user,
    get_quiz_schedule_by_id,
    get_quiz_set_by_id,
    get_user_quiz_schedules,
    get_user_quiz_sets,
)
from app.services.telegram_service import TelegramService
from app.utils.logger import setup_logger

logger = setup_logger(__name__)
telegram_service = TelegramService()

DEFAULT_TIMEZONE = "Asia/Kolkata"
SCHEDULES_PER_PAGE = 5
QUIZZES_PER_PAGE = 6


# ============================================================================
# Automation Hub Menu
# ============================================================================


async def automation_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the Quiz Automation Engine main hub menu."""
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "⏰ <b>Quiz Automation Engine</b>\n\n"
        "Schedule quizzes to be automatically conducted in your Telegram groups and channels "
        "at designated dates and times.\n\n"
        "• <b>Mode:</b> Single Quiz Scheduling (Phase 1)\n"
        "• <b>Timezone:</b> Configurable per schedule (default: <code>Asia/Kolkata</code>)\n"
        "• <b>Reliability:</b> Background worker with restart recovery & status tracking\n\n"
        "<i>Select an option below to get started:</i>"
    )

    kb = get_automation_menu_keyboard()
    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# ============================================================================
# Scheduling Conversation Flow Handlers
# ============================================================================


def _clean_schedule_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove transient scheduling keys from context.user_data."""
    keys = [
        "schedule_quiz_id",
        "schedule_quiz_title",
        "schedule_quiz_q_count",
        "schedule_chat_id",
        "schedule_chat_title",
        "schedule_date",
        "schedule_time",
        "schedule_tz",
        "schedule_scheduled_at_utc",
        "waiting_for_custom_group",
        "waiting_for_custom_date",
        "waiting_for_custom_time",
        "waiting_for_custom_tz",
    ]
    for k in keys:
        context.user_data.pop(k, None)


def _get_known_groups_for_user(user_id: int) -> list[dict]:
    """Retrieve known groups from GroupMember table where this user has been seen."""
    with SessionLocal() as db:
        memberships = (
            db.query(GroupMember)
            .filter(GroupMember.user_id == user_id, GroupMember.is_active == True)
            .all()
        )
        seen_chats = set()
        groups = []
        for m in memberships:
            if m.chat_id not in seen_chats:
                seen_chats.add(m.chat_id)
                groups.append({"chat_id": str(m.chat_id), "title": f"Group ({m.chat_id})"})
        return groups


async def start_schedule_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point to schedule a quiz from saved quiz list."""
    query = update.callback_query
    if query:
        await query.answer()

    _clean_schedule_session(context)
    context.user_data["schedule_tz"] = DEFAULT_TIMEZONE

    user = update.effective_user
    if not user:
        return ConversationHandler.END

    with SessionLocal() as db:
        quiz_sets, total_count = get_user_quiz_sets(
            db, telegram_id=user.id, limit=QUIZZES_PER_PAGE, offset=0
        )

    if total_count == 0:
        no_quiz_text = (
            "⚠️ <b>No Saved Quizzes Found!</b>\n\n"
            "You need at least one saved quiz before you can schedule an automated conduction.\n\n"
            "Use /newquiz to create a quiz batch first."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✍️ Create a Quiz", callback_data="action_bulk_create")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu")],
            ]
        )
        if query and query.message:
            await query.edit_message_text(no_quiz_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text(
                no_quiz_text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        return ConversationHandler.END

    text = (
        "⏰ <b>Schedule a Quiz (Step 1 of 5)</b>\n\n"
        "Select the quiz you would like to schedule for automated publishing:"
    )
    kb = get_schedule_quizzes_keyboard(
        quiz_sets, page=0, total_count=total_count, per_page=QUIZZES_PER_PAGE
    )

    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_QUIZ


async def schedule_quiz_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paginate through quizzes during schedule selection."""
    query = update.callback_query
    if not query or not query.data:
        return QuizScheduleState.SELECTING_QUIZ
    await query.answer()

    page = int(query.data.split("_")[-1])
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    with SessionLocal() as db:
        quiz_sets, total_count = get_user_quiz_sets(
            db, telegram_id=user.id, limit=QUIZZES_PER_PAGE, offset=page * QUIZZES_PER_PAGE
        )

    text = (
        "⏰ <b>Schedule a Quiz (Step 1 of 5)</b>\n\n"
        "Select the quiz you would like to schedule for automated publishing:"
    )
    kb = get_schedule_quizzes_keyboard(
        quiz_sets, page=page, total_count=total_count, per_page=QUIZZES_PER_PAGE
    )

    if query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_QUIZ


async def start_schedule_from_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point directly from a specific quiz details card."""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()

    _clean_schedule_session(context)
    context.user_data["schedule_tz"] = DEFAULT_TIMEZONE

    quiz_set_id = int(query.data.split("_")[-1])
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id, telegram_id=user.id)
        if not quiz_set:
            await query.answer("Quiz not found or unauthorized.", show_alert=True)
            return ConversationHandler.END

        context.user_data["schedule_quiz_id"] = quiz_set.id
        context.user_data["schedule_quiz_title"] = quiz_set.title
        context.user_data["schedule_quiz_q_count"] = len(quiz_set.questions)

    return await prompt_group_selection(update, context)


async def pick_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handler when user selects a quiz from the list."""
    query = update.callback_query
    if not query or not query.data:
        return QuizScheduleState.SELECTING_QUIZ
    await query.answer()

    quiz_set_id = int(query.data.split("_")[-1])
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id, telegram_id=user.id)
        if not quiz_set:
            await query.answer("Quiz not found or unauthorized.", show_alert=True)
            return QuizScheduleState.SELECTING_QUIZ

        context.user_data["schedule_quiz_id"] = quiz_set.id
        context.user_data["schedule_quiz_title"] = quiz_set.title
        context.user_data["schedule_quiz_q_count"] = len(quiz_set.questions)

    return await prompt_group_selection(update, context)


async def prompt_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show group selection interface."""
    user = update.effective_user
    user_id = user.id if user else 0
    quiz_title = context.user_data.get("schedule_quiz_title", "Selected Quiz")
    q_count = context.user_data.get("schedule_quiz_q_count", 0)

    known_groups = _get_known_groups_for_user(user_id)
    text = (
        f"🎯 <b>Selected Quiz:</b> <b>{html.escape(quiz_title)}</b> (<code>{q_count} Questions</code>)\n\n"
        "<b>Step 2 of 5: Select Destination Group</b>\n\n"
        "Choose a Telegram group or channel where this quiz should be automatically conducted.\n\n"
        "⚠️ <i>Ensure QuizBotPro is added to the target group as an <b>Administrator</b> with 'Post Messages' rights.</i>"
    )

    kb = get_schedule_groups_keyboard(known_groups)
    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_GROUP


async def pick_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 1-tap selection of a known group."""
    query = update.callback_query
    if not query or not query.data:
        return QuizScheduleState.SELECTING_GROUP
    await query.answer()

    target_chat_id = query.data.replace("sched_group_", "")
    return await _validate_and_advance_group(update, context, target_chat_id)


async def prompt_type_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type a group username or ID."""
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "✏️ <b>Enter Destination Group or Channel</b>\n\n"
        "Please send the <b>@username</b> (e.g. <code>@MyQuizGroup</code>) or numeric chat ID (e.g. <code>-1001234567890</code>) below:\n\n"
        "⚠️ <i>Make sure the bot is already an Admin in that group before submitting.</i>"
    )
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back", callback_data="sched_back_to_group")]]
    )
    context.user_data["waiting_for_custom_group"] = True

    if query and query.message:
        await query.edit_message_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_GROUP


async def receive_group_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive typed group username or chat ID."""
    if not update.message or not update.message.text:
        return QuizScheduleState.SELECTING_GROUP

    text = update.message.text.strip()
    return await _validate_and_advance_group(update, context, text)


async def _validate_and_advance_group(
    update: Update, context: ContextTypes.DEFAULT_TYPE, raw_target: str
) -> int:
    """Validate bot permissions in target group and proceed to date selection."""
    bot = context.bot
    valid, msg, chat_id = await telegram_service.validate_channel_access(bot, raw_target)

    if not valid:
        err_text = (
            f"❌ <b>Group Verification Failed</b>\n\n"
            f"{msg}\n\n"
            "Please add the bot as an Administrator with 'Post Messages' rights, or enter a different group username/ID:"
        )
        retry_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✏️ Enter Another Group", callback_data="sched_type_group")],
                [
                    InlineKeyboardButton(
                        "🔙 Back to Quiz Selection", callback_data="sched_back_to_quiz"
                    )
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="action_cancel_schedule")],
            ]
        )
        if update.callback_query and update.callback_query.message:
            await update.callback_query.edit_message_text(
                err_text, reply_markup=retry_kb, parse_mode=ParseMode.HTML
            )
        elif update.message:
            await update.message.reply_text(
                err_text, reply_markup=retry_kb, parse_mode=ParseMode.HTML
            )
        return QuizScheduleState.SELECTING_GROUP

    # Group verified!
    target_id_str = str(chat_id) if chat_id is not None else raw_target
    chat_title = raw_target
    try:
        chat_obj = await bot.get_chat(target_id_str)
        if chat_obj and chat_obj.title:
            chat_title = chat_obj.title
    except Exception:  # noqa: BLE001, S110
        pass

    context.user_data["schedule_chat_id"] = target_id_str
    context.user_data["schedule_chat_title"] = chat_title
    context.user_data.pop("waiting_for_custom_group", None)

    return await prompt_date_selection(update, context)


async def prompt_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show date selection interface."""
    user_tz = context.user_data.get("schedule_tz", DEFAULT_TIMEZONE)
    chat_title = context.user_data.get("schedule_chat_title", "Target Group")
    quiz_title = context.user_data.get("schedule_quiz_title", "Selected Quiz")

    text = (
        f"🎯 <b>Quiz:</b> <b>{html.escape(quiz_title)}</b>\n"
        f"👥 <b>Target:</b> <b>{html.escape(chat_title)}</b>\n\n"
        "<b>Step 3 of 5: Select Schedule Date</b>\n\n"
        f"Select the date when this quiz should be conducted (Timezone: <code>{user_tz}</code>):"
    )

    kb = get_schedule_date_keyboard(user_tz)
    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_DATE


async def pick_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 1-tap date selection."""
    query = update.callback_query
    if not query or not query.data:
        return QuizScheduleState.SELECTING_DATE
    await query.answer()

    date_str = query.data.replace("sched_date_", "")
    context.user_data["schedule_date"] = date_str
    return await prompt_time_selection(update, context)


async def prompt_type_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type a custom date."""
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "✏️ <b>Enter Custom Date</b>\n\n"
        "Please type your desired date in <b>YYYY-MM-DD</b> format (e.g. <code>2026-11-25</code>):"
    )
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back to Date Selection", callback_data="sched_back_to_date")]]
    )
    context.user_data["waiting_for_custom_date"] = True

    if query and query.message:
        await query.edit_message_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_DATE


async def receive_date_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and receive user-typed custom date."""
    if not update.message or not update.message.text:
        return QuizScheduleState.SELECTING_DATE

    text = update.message.text.strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if not match:
        await update.message.reply_text(
            "⚠️ Invalid date format. Please send in <b>YYYY-MM-DD</b> format (e.g. <code>2026-11-25</code>):",
            parse_mode=ParseMode.HTML,
        )
        return QuizScheduleState.SELECTING_DATE

    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        parsed_date = datetime(year, month, day).date()
    except ValueError:
        await update.message.reply_text(
            "⚠️ Invalid calendar date. Please enter a valid date (YYYY-MM-DD):",
            parse_mode=ParseMode.HTML,
        )
        return QuizScheduleState.SELECTING_DATE

    user_tz = context.user_data.get("schedule_tz", DEFAULT_TIMEZONE)
    try:
        tz = ZoneInfo(user_tz)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Asia/Kolkata")

    today = datetime.now(tz).date()
    if parsed_date < today:
        await update.message.reply_text(
            f"⚠️ Date cannot be in the past! Today is <code>{today.strftime('%Y-%m-%d')}</code>. Please enter a future date:",
            parse_mode=ParseMode.HTML,
        )
        return QuizScheduleState.SELECTING_DATE

    context.user_data["schedule_date"] = parsed_date.strftime("%Y-%m-%d")
    context.user_data.pop("waiting_for_custom_date", None)

    return await prompt_time_selection(update, context)


async def prompt_time_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show time selection interface."""
    date_str = context.user_data.get("schedule_date", "")
    user_tz = context.user_data.get("schedule_tz", DEFAULT_TIMEZONE)
    chat_title = context.user_data.get("schedule_chat_title", "Target Group")
    quiz_title = context.user_data.get("schedule_quiz_title", "Selected Quiz")

    text = (
        f"🎯 <b>Quiz:</b> <b>{html.escape(quiz_title)}</b>\n"
        f"👥 <b>Target:</b> <b>{html.escape(chat_title)}</b>\n"
        f"📅 <b>Date:</b> <code>{date_str}</code>\n\n"
        "<b>Step 4 of 5: Select Schedule Time</b>\n\n"
        f"Choose a time slot or enter a custom time (Timezone: <code>{user_tz}</code>):"
    )

    kb = get_schedule_time_keyboard(date_str, user_tz)
    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_TIME


async def pick_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 1-tap time selection."""
    query = update.callback_query
    if not query or not query.data:
        return QuizScheduleState.SELECTING_TIME
    await query.answer()

    time_str = query.data.replace("sched_time_", "")
    return await _validate_and_advance_time(update, context, time_str)


async def prompt_type_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type a custom time."""
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "✏️ <b>Enter Custom Time</b>\n\n"
        "Please type your desired time in 12-hour or 24-hour format below:\n"
        "• <code>07:30 PM</code>\n"
        "• <code>19:30</code>\n"
        "• <code>8:00 AM</code>"
    )
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back to Time Slots", callback_data="sched_back_to_time")]]
    )
    context.user_data["waiting_for_custom_time"] = True

    if query and query.message:
        await query.edit_message_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_TIME


def _parse_time_string(raw: str) -> time | None:
    """Parse flexible time strings into a datetime.time object."""
    cleaned = raw.strip().upper()
    # 1. 24-hour format: HH:MM or H:MM
    match24 = re.match(r"^(\d{1,2}):(\d{2})$", cleaned)
    if match24:
        h, m = int(match24.group(1)), int(match24.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return time(h, m)

    # 2. 12-hour format: HH:MM AM/PM or H:MM AM/PM
    match12 = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)$", cleaned)
    if match12:
        h, m, ampm = int(match12.group(1)), int(match12.group(2)), match12.group(3)
        if 1 <= h <= 12 and 0 <= m <= 59:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
            return time(h, m)

    # 3. Simple hour with AM/PM: 7PM, 7 PM, 11AM
    match_simple = re.match(r"^(\d{1,2})\s*(AM|PM)$", cleaned)
    if match_simple:
        h, ampm = int(match_simple.group(1)), match_simple.group(2)
        if 1 <= h <= 12:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
            return time(h, 0)

    return None


async def receive_time_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and receive user-typed custom time."""
    if not update.message or not update.message.text:
        return QuizScheduleState.SELECTING_TIME

    raw_text = update.message.text.strip()
    parsed_time = _parse_time_string(raw_text)
    if not parsed_time:
        await update.message.reply_text(
            "⚠️ Invalid time format. Please enter like <code>07:30 PM</code> or <code>19:30</code>:",
            parse_mode=ParseMode.HTML,
        )
        return QuizScheduleState.SELECTING_TIME

    time_str = parsed_time.strftime("%H:%M")
    return await _validate_and_advance_time(update, context, time_str)


async def _validate_and_advance_time(
    update: Update, context: ContextTypes.DEFAULT_TYPE, time_str: str
) -> int:
    """Validate that combined (date, time, timezone) is in the future, then show confirmation."""
    date_str = context.user_data.get("schedule_date")
    user_tz = context.user_data.get("schedule_tz", DEFAULT_TIMEZONE)

    if not date_str:
        return await prompt_date_selection(update, context)

    try:
        tz = ZoneInfo(user_tz)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Asia/Kolkata")

    # Parse components into local datetime
    h, m = map(int, time_str.split(":"))
    y, mon, d = map(int, date_str.split("-"))
    local_dt = datetime(y, mon, d, h, m, 0, tzinfo=tz)

    now_local = datetime.now(tz)
    if local_dt <= now_local:
        warn_text = (
            f"⚠️ <b>Scheduled time must be in the future!</b>\n\n"
            f"You selected: <code>{local_dt.strftime('%d %b %Y, %I:%M %p')}</code>\n"
            f"Current time: <code>{now_local.strftime('%d %b %Y, %I:%M %p')} ({user_tz})</code>\n\n"
            "Please select a future time slot or choose another date."
        )
        kb = get_schedule_time_keyboard(date_str, user_tz)
        if update.callback_query and update.callback_query.message:
            await update.callback_query.edit_message_text(
                warn_text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        elif update.message:
            await update.message.reply_text(warn_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return QuizScheduleState.SELECTING_TIME

    # Convert to UTC
    utc_dt = local_dt.astimezone(UTC)
    context.user_data["schedule_time"] = time_str
    context.user_data["schedule_scheduled_at_utc"] = utc_dt
    context.user_data.pop("waiting_for_custom_time", None)

    return await prompt_confirmation(update, context)


async def prompt_timezone_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Show timezone selection interface."""
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "🌐 <b>Select Timezone</b>\n\n"
        "Choose your preferred timezone for scheduling:\n\n"
        "<i>(All timestamps are stored in UTC internally and converted automatically.)</i>"
    )
    kb = get_schedule_timezone_keyboard()

    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_TIMEZONE


async def set_timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set timezone from preset and return to time selection."""
    query = update.callback_query
    if not query or not query.data:
        return QuizScheduleState.SELECTING_TIMEZONE
    await query.answer()

    tz_name = query.data.replace("sched_set_tz_", "")
    try:
        ZoneInfo(tz_name)
        context.user_data["schedule_tz"] = tz_name
    except Exception:  # noqa: BLE001
        context.user_data["schedule_tz"] = DEFAULT_TIMEZONE

    return await prompt_time_selection(update, context)


async def prompt_type_timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type an IANA timezone name."""
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "✏️ <b>Enter Timezone Name</b>\n\n"
        "Send your IANA timezone identifier (e.g. <code>Asia/Kolkata</code>, <code>UTC</code>, <code>Europe/Berlin</code>):"
    )
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back to Timezones", callback_data="sched_select_tz")]]
    )
    context.user_data["waiting_for_custom_tz"] = True

    if query and query.message:
        await query.edit_message_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.SELECTING_TIMEZONE


async def receive_timezone_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and receive typed timezone string."""
    if not update.message or not update.message.text:
        return QuizScheduleState.SELECTING_TIMEZONE

    tz_input = update.message.text.strip()
    try:
        ZoneInfo(tz_input)
        context.user_data["schedule_tz"] = tz_input
        context.user_data.pop("waiting_for_custom_tz", None)
        return await prompt_time_selection(update, context)
    except Exception:  # noqa: BLE001
        await update.message.reply_text(
            "⚠️ Unknown timezone. Please send a valid IANA timezone (e.g. <code>Asia/Kolkata</code>, <code>UTC</code>):",
            parse_mode=ParseMode.HTML,
        )
        return QuizScheduleState.SELECTING_TIMEZONE


async def prompt_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Display final review summary and confirm button."""
    quiz_title = context.user_data.get("schedule_quiz_title", "Quiz")
    q_count = context.user_data.get("schedule_quiz_q_count", 0)
    chat_title = context.user_data.get("schedule_chat_title", "Target Group")
    chat_id = context.user_data.get("schedule_chat_id", "")
    date_str = context.user_data.get("schedule_date", "")
    time_str = context.user_data.get("schedule_time", "")
    user_tz = context.user_data.get("schedule_tz", DEFAULT_TIMEZONE)
    utc_dt = context.user_data.get("schedule_scheduled_at_utc")

    # Format 12-hour display
    h, m = map(int, time_str.split(":"))
    parsed_time = time(h, m)
    time_display = parsed_time.strftime("%I:%M %p")
    utc_display = utc_dt.strftime("%Y-%m-%d %H:%M UTC") if utc_dt else "N/A"

    summary_text = (
        "📋 <b>Review Schedule Details (Step 5 of 5)</b>\n\n"
        f"• <b>Quiz:</b> <b>{html.escape(quiz_title)}</b> (<code>{q_count} Questions</code>)\n"
        f"• <b>Destination:</b> <b>{html.escape(chat_title)}</b> (<code>{chat_id}</code>)\n"
        f"• <b>Date:</b> <code>{date_str}</code>\n"
        f"• <b>Time:</b> <code>{time_display}</code> ({time_str})\n"
        f"• <b>Timezone:</b> <code>{user_tz}</code>\n"
        f"• <b>Scheduled (UTC):</b> <code>{utc_display}</code>\n\n"
        "⚡ <i>The bot will automatically conduct this quiz at the scheduled time.</i>\n\n"
        "Tap <b>✅ Confirm Schedule</b> to finalize:"
    )

    kb = get_schedule_confirm_keyboard()
    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(summary_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(summary_text, reply_markup=kb, parse_mode=ParseMode.HTML)

    return QuizScheduleState.CONFIRMING_SCHEDULE


async def confirm_schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Persist the confirmed schedule to the database."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return ConversationHandler.END

    quiz_id = context.user_data.get("schedule_quiz_id")
    chat_id = context.user_data.get("schedule_chat_id")
    chat_title = context.user_data.get("schedule_chat_title")
    user_tz = context.user_data.get("schedule_tz", DEFAULT_TIMEZONE)
    utc_dt = context.user_data.get("schedule_scheduled_at_utc")
    quiz_title = context.user_data.get("schedule_quiz_title", "Quiz")
    date_str = context.user_data.get("schedule_date", "")
    time_str = context.user_data.get("schedule_time", "")

    if not quiz_id or not chat_id or not utc_dt:
        if query and query.message:
            await query.edit_message_text("⚠️ Scheduling session expired. Please start again.")
        return ConversationHandler.END

    try:
        with SessionLocal() as db:
            user_rec = get_or_create_user(db, telegram_id=user.id, username=user.username)
            schedule = create_quiz_schedule(
                db=db,
                user_id=user_rec.id,
                quiz_set_id=quiz_id,
                chat_id=chat_id,
                scheduled_at=utc_dt,
                timezone_str=user_tz,
                chat_title=chat_title,
            )
            schedule_id = schedule.id
    except Exception as e:
        logger.exception("Failed to create quiz schedule")
        if query and query.message:
            await query.edit_message_text(
                f"❌ <b>Database Error:</b> Could not save schedule.\n<i>{html.escape(str(e))}</i>",
                parse_mode=ParseMode.HTML,
            )
        return ConversationHandler.END

    _clean_schedule_session(context)

    # Success receipt
    receipt_text = (
        "🎉 <b>Quiz Scheduled Successfully!</b>\n\n"
        f"• <b>Schedule ID:</b> <code>#{schedule_id}</code>\n"
        f"• <b>Quiz:</b> <b>{html.escape(quiz_title)}</b>\n"
        f"• <b>Destination:</b> <b>{html.escape(chat_title or chat_id)}</b>\n"
        f"• <b>Execution Time:</b> <code>{date_str} at {time_str} ({user_tz})</code>\n"
        f"• <b>UTC Time:</b> <code>{utc_dt.strftime('%Y-%m-%d %H:%M UTC')}</code>\n\n"
        "🔔 <i>You will receive a notification in this chat once the quiz has been conducted.</i>"
    )

    action_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 View My Schedules", callback_data="action_my_schedules_0"),
            ],
            [
                InlineKeyboardButton(
                    "➕ Schedule Another Quiz", callback_data="action_schedule_start"
                ),
                InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu"),
            ],
        ]
    )

    if query and query.message:
        await query.edit_message_text(
            receipt_text, reply_markup=action_kb, parse_mode=ParseMode.HTML
        )
    elif update.message:
        await update.message.reply_text(
            receipt_text, reply_markup=action_kb, parse_mode=ParseMode.HTML
        )

    return ConversationHandler.END


async def cancel_schedule_flow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel out of the scheduling conversation."""
    query = update.callback_query
    if query:
        await query.answer("Scheduling cancelled.")

    _clean_schedule_session(context)

    text = "🚫 <b>Quiz scheduling cancelled.</b>\n\nReturning to the Quiz Automation menu:"
    kb = get_automation_menu_keyboard()

    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    return ConversationHandler.END


# ============================================================================
# Schedule Inspection & Cancellation Handlers (Outside Conversation)
# ============================================================================


async def my_schedules_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display paginated list of user's schedules."""
    query = update.callback_query
    if query:
        await query.answer()

    page = 0
    if query and query.data and query.data.startswith("my_sched_page_"):
        try:
            page = int(query.data.split("_")[-1])
        except ValueError:
            page = 0

    user = update.effective_user
    if not user:
        return

    with SessionLocal() as db:
        user_rec = get_or_create_user(db, telegram_id=user.id, username=user.username)
        schedules, total_count = get_user_quiz_schedules(
            db, user_id=user_rec.id, limit=SCHEDULES_PER_PAGE, offset=page * SCHEDULES_PER_PAGE
        )

    if total_count == 0:
        empty_text = (
            "📋 <b>My Scheduled Quizzes</b>\n\n"
            "<i>You do not have any scheduled quizzes yet!</i>\n\n"
            "Tap below to schedule your first quiz:"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⏰ Schedule a Quiz", callback_data="action_schedule_start")],
                [
                    InlineKeyboardButton(
                        "🔙 Automation Menu", callback_data="action_automation_menu"
                    )
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu")],
            ]
        )
        if query and query.message:
            await query.edit_message_text(empty_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text(empty_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    text = (
        f"📋 <b>My Scheduled Quizzes (Page {page + 1})</b>\n\n"
        f"You have <b>{total_count}</b> total schedule record(s).\n"
        "Tap on any schedule below to inspect details or cancel:"
    )
    kb = get_my_schedules_keyboard(
        schedules, page=page, total_count=total_count, per_page=SCHEDULES_PER_PAGE
    )

    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def view_schedule_details_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Display comprehensive details for a specific schedule."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    schedule_id = int(query.data.split("_")[-1])
    user = update.effective_user
    if not user:
        return

    with SessionLocal() as db:
        user_rec = get_or_create_user(db, telegram_id=user.id, username=user.username)
        schedule = get_quiz_schedule_by_id(db, schedule_id, user_id=user_rec.id)

        if not schedule:
            await query.answer("Schedule not found or unauthorized.", show_alert=True)
            return

        status_emojis = {
            "pending": "⏳ Pending Execution",
            "running": "🚀 Currently Executing",
            "completed": "✅ Completed Successfully",
            "failed": "❌ Failed",
            "cancelled": "🚫 Cancelled by User",
        }
        status_display = status_emojis.get(schedule.status, schedule.status)

        # Convert UTC scheduled time to user timezone for display
        try:
            tz = ZoneInfo(schedule.timezone)
            scheduled_local = schedule.scheduled_at.replace(tzinfo=UTC).astimezone(tz)
            local_time_str = scheduled_local.strftime("%Y-%m-%d %I:%M %p")
        except Exception:  # noqa: BLE001
            local_time_str = schedule.scheduled_at.strftime("%Y-%m-%d %H:%M UTC")

        utc_time_str = schedule.scheduled_at.strftime("%Y-%m-%d %H:%M UTC")
        created_str = schedule.created_at.strftime("%Y-%m-%d %H:%M UTC")
        executed_str = (
            schedule.executed_at.strftime("%Y-%m-%d %H:%M UTC")
            if schedule.executed_at
            else "Not yet executed"
        )
        quiz_title = (
            schedule.quiz_set.title if schedule.quiz_set else f"Quiz {schedule.quiz_set_id}"
        )
        q_count = len(schedule.quiz_set.questions) if schedule.quiz_set else 0

        err_section = ""
        if schedule.error_message:
            err_section = f"\n⚠️ <b>Error:</b> <i>{html.escape(schedule.error_message)}</i>\n"

        details_text = (
            f"⏰ <b>Schedule Details: #{schedule.id}</b>\n\n"
            f"• <b>Status:</b> <b>{status_display}</b>\n"
            f"• <b>Quiz:</b> <b>{html.escape(quiz_title)}</b> (<code>{q_count} Qs</code>)\n"
            f"• <b>Target:</b> <b>{html.escape(schedule.chat_title or schedule.chat_id)}</b>\n"
            f"• <b>Target ID:</b> <code>{schedule.chat_id}</code>\n"
            f"• <b>Scheduled For:</b> <code>{local_time_str}</code>\n"
            f"• <b>Timezone:</b> <code>{schedule.timezone}</code>\n"
            f"• <b>UTC Time:</b> <code>{utc_time_str}</code>\n"
            f"• <b>Created:</b> <code>{created_str}</code>\n"
            f"• <b>Executed:</b> <code>{executed_str}</code>\n"
            f"{err_section}"
        )

        is_pending = schedule.status == "pending"

    kb = get_schedule_details_keyboard(schedule_id, is_pending=is_pending)
    if query.message:
        await query.edit_message_text(details_text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def cancel_schedule_by_id_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle cancelling a specific schedule from its details view."""
    query = update.callback_query
    if not query or not query.data:
        return

    schedule_id = int(query.data.split("_")[-1])
    user = update.effective_user
    if not user:
        return

    with SessionLocal() as db:
        user_rec = get_or_create_user(db, telegram_id=user.id, username=user.username)
        success, msg, _schedule = cancel_quiz_schedule(db, schedule_id, user_rec.id)

    if success:
        await query.answer("Schedule cancelled successfully!")
        # Return to schedule details
        query.data = f"view_schedule_{schedule_id}"
        await view_schedule_details_callback(update, context)
    else:
        await query.answer(f"Failed: {msg}", show_alert=True)


async def cancel_schedule_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display user's active pending schedules for direct 1-tap cancellation."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return

    with SessionLocal() as db:
        user_rec = get_or_create_user(db, telegram_id=user.id, username=user.username)
        pending_schedules, total_count = get_user_quiz_schedules(
            db, user_id=user_rec.id, status="pending", limit=10, offset=0
        )

    if total_count == 0:
        empty_text = (
            "❌ <b>Cancel a Schedule</b>\n\n"
            "<i>You do not have any pending schedules to cancel.</i>"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📋 View All Schedules", callback_data="action_my_schedules_0"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Automation Menu", callback_data="action_automation_menu"
                    )
                ],
            ]
        )
        if query and query.message:
            await query.edit_message_text(empty_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text(empty_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    rows: list[list[InlineKeyboardButton]] = []
    for s in pending_schedules:
        date_str = s.scheduled_at.strftime("%d %b %H:%M UTC")
        quiz_title = s.quiz_set.title if s.quiz_set else f"Quiz {s.quiz_set_id}"
        snippet = quiz_title[:18] + ".." if len(quiz_title) > 18 else quiz_title
        btn_text = f"❌ Cancel #{s.id}: {snippet} ({date_str})"
        rows.append([InlineKeyboardButton(btn_text, callback_data=f"sched_cancel_id_{s.id}")])

    rows.append(
        [
            InlineKeyboardButton("🔙 Automation Menu", callback_data="action_automation_menu"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu"),
        ]
    )

    text = (
        "❌ <b>Cancel a Pending Schedule</b>\n\n"
        "Tap on any pending schedule below to cancel its upcoming execution:"
    )
    kb = InlineKeyboardMarkup(rows)

    if query and query.message:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def sched_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """No-op callback for passive indicators."""
    query = update.callback_query
    if query:
        await query.answer()
