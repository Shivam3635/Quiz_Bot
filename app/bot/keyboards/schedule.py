"""Inline keyboards for Phase 1: Single Quiz Scheduling."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.models import QuizSchedule, QuizSet


def get_automation_menu_keyboard() -> InlineKeyboardMarkup:
    """Main hub keyboard for Quiz Automation engine."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏰ Schedule a Quiz", callback_data="action_schedule_start"),
            ],
            [
                InlineKeyboardButton(
                    "📋 My Scheduled Quizzes", callback_data="action_my_schedules_0"
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel a Schedule", callback_data="action_cancel_schedule_menu"
                ),
            ],
            [
                InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu"),
            ],
        ]
    )


def get_schedule_quizzes_keyboard(
    quiz_sets: list[QuizSet],
    page: int = 0,
    total_count: int = 0,
    per_page: int = 6,
) -> InlineKeyboardMarkup:
    """Paginated list of quizzes for user to select to schedule."""
    rows: list[list[InlineKeyboardButton]] = []

    for idx, qs in enumerate(quiz_sets, start=page * per_page + 1):
        q_count = len(qs.questions)
        title_snippet = qs.title[:22] + "..." if len(qs.title) > 22 else qs.title
        btn_text = f"{idx}. {title_snippet} ({q_count} Qs)"
        rows.append([InlineKeyboardButton(btn_text, callback_data=f"sched_pick_quiz_{qs.id}")])

    # Pagination controls
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"sched_quiz_page_{page - 1}"))
    nav_row.append(
        InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="sched_noop")
    )
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"sched_quiz_page_{page + 1}"))

    if len(nav_row) > 1:
        rows.append(nav_row)

    rows.append(
        [InlineKeyboardButton("❌ Cancel Scheduling", callback_data="action_cancel_schedule")]
    )
    return InlineKeyboardMarkup(rows)


def get_schedule_groups_keyboard(
    known_groups: list[dict] | None = None,
) -> InlineKeyboardMarkup:
    """Keyboard for selecting destination Telegram group."""
    rows: list[list[InlineKeyboardButton]] = []

    if known_groups:
        for g in known_groups[:5]:
            title = g.get("title") or f"Group {g.get('chat_id')}"
            cid = g.get("chat_id")
            rows.append([InlineKeyboardButton(f"👥 {title}", callback_data=f"sched_group_{cid}")])

    rows.append(
        [
            InlineKeyboardButton("✏️ Enter @Channel / Group ID", callback_data="sched_type_group"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("🔙 Back to Quiz Selection", callback_data="sched_back_to_quiz"),
            InlineKeyboardButton("❌ Cancel", callback_data="action_cancel_schedule"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_schedule_date_keyboard(user_tz: str = "Asia/Kolkata") -> InlineKeyboardMarkup:
    """Return date selection options computed relative to user's timezone."""
    try:
        tz = ZoneInfo(user_tz)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Asia/Kolkata")

    now = datetime.now(tz)
    rows: list[list[InlineKeyboardButton]] = []

    # Next 6 days
    date_buttons = []
    for day_offset in range(6):
        target_date = now.date() + timedelta(days=day_offset)
        iso_str = target_date.strftime("%Y-%m-%d")

        if day_offset == 0:
            label = f"Today ({target_date.strftime('%d %b')})"
        elif day_offset == 1:
            label = f"Tomorrow ({target_date.strftime('%d %b')})"
        else:
            label = target_date.strftime("%a, %d %b")

        date_buttons.append(InlineKeyboardButton(label, callback_data=f"sched_date_{iso_str}"))

    # Arrange in pairs
    for i in range(0, len(date_buttons), 2):
        rows.append(date_buttons[i : i + 2])

    rows.append(
        [
            InlineKeyboardButton("✏️ Custom Date (YYYY-MM-DD)", callback_data="sched_type_date"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("🔙 Back to Group Selection", callback_data="sched_back_to_group"),
            InlineKeyboardButton("❌ Cancel", callback_data="action_cancel_schedule"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_schedule_time_keyboard(
    selected_date_str: str,
    user_tz: str = "Asia/Kolkata",
) -> InlineKeyboardMarkup:
    """Return convenient time slot buttons with timezone switch."""
    time_slots = [
        ("09:00 AM", "09:00"),
        ("11:00 AM", "11:00"),
        ("02:00 PM", "14:00"),
        ("04:00 PM", "16:00"),
        ("06:00 PM", "18:00"),
        ("07:00 PM", "19:00"),
        ("08:00 PM", "20:00"),
        ("09:00 PM", "21:00"),
    ]

    rows: list[list[InlineKeyboardButton]] = []
    slot_buttons = [
        InlineKeyboardButton(label, callback_data=f"sched_time_{time_val}")
        for label, time_val in time_slots
    ]

    for i in range(0, len(slot_buttons), 2):
        rows.append(slot_buttons[i : i + 2])

    rows.append(
        [
            InlineKeyboardButton(f"🌐 Timezone: {user_tz}", callback_data="sched_select_tz"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("✏️ Custom Time (e.g. 7:30 PM)", callback_data="sched_type_time"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("🔙 Back to Date", callback_data="sched_back_to_date"),
            InlineKeyboardButton("❌ Cancel", callback_data="action_cancel_schedule"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_schedule_timezone_keyboard() -> InlineKeyboardMarkup:
    """Quick timezone selection keyboard with major zones."""
    common_tzs = [
        ("🇮🇳 India Standard Time (IST)", "Asia/Kolkata"),
        ("🌐 UTC (Coordinated Universal Time)", "UTC"),
        ("🇺🇸 US Eastern (EST/EDT)", "America/New_York"),
        ("🇺🇸 US Pacific (PST/PDT)", "America/Los_Angeles"),
        ("🇬🇧 London (GMT/BST)", "Europe/London"),
        ("🇦🇪 Dubai (GST)", "Asia/Dubai"),
        ("🇸🇬 Singapore (SGT)", "Asia/Singapore"),
    ]

    rows = [
        [InlineKeyboardButton(label, callback_data=f"sched_set_tz_{tz_name}")]
        for label, tz_name in common_tzs
    ]

    rows.append(
        [
            InlineKeyboardButton("✏️ Custom Timezone Name", callback_data="sched_type_tz"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("🔙 Back to Time Slots", callback_data="sched_back_to_time"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_schedule_confirm_keyboard() -> InlineKeyboardMarkup:
    """Review and confirm scheduling details."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm Schedule", callback_data="sched_confirm_schedule"),
            ],
            [
                InlineKeyboardButton("✏️ Edit Group", callback_data="sched_back_to_group"),
                InlineKeyboardButton("✏️ Edit Date/Time", callback_data="sched_back_to_date"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="action_cancel_schedule"),
            ],
        ]
    )


def get_my_schedules_keyboard(
    schedules: list[QuizSchedule],
    page: int = 0,
    total_count: int = 0,
    per_page: int = 5,
) -> InlineKeyboardMarkup:
    """Paginated list of user's past and upcoming schedules."""
    rows: list[list[InlineKeyboardButton]] = []

    status_emojis = {
        "pending": "⏳ Pending",
        "running": "🚀 Running",
        "completed": "✅ Done",
        "failed": "❌ Failed",
        "cancelled": "🚫 Cancelled",
    }

    for idx, s in enumerate(schedules, start=page * per_page + 1):
        status_tag = status_emojis.get(s.status, s.status)
        date_str = s.scheduled_at.strftime("%d %b %H:%M")
        quiz_title = s.quiz_set.title if s.quiz_set else f"Quiz {s.quiz_set_id}"
        snippet = quiz_title[:18] + ".." if len(quiz_title) > 18 else quiz_title
        btn_text = f"{idx}. {snippet} | {date_str} [{status_tag}]"
        rows.append([InlineKeyboardButton(btn_text, callback_data=f"view_schedule_{s.id}")])

    total_pages = max(1, (total_count + per_page - 1) // per_page)
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"my_sched_page_{page - 1}"))
    nav_row.append(
        InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="sched_noop")
    )
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_sched_page_{page + 1}"))

    if len(nav_row) > 1:
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton("➕ Schedule Another Quiz", callback_data="action_schedule_start"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_schedule_details_keyboard(
    schedule_id: int,
    is_pending: bool = False,
) -> InlineKeyboardMarkup:
    """Controls for inspecting a specific schedule."""
    rows: list[list[InlineKeyboardButton]] = []

    if is_pending:
        rows.append(
            [
                InlineKeyboardButton(
                    "❌ Cancel This Schedule", callback_data=f"sched_cancel_id_{schedule_id}"
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton("📋 Back to Schedules", callback_data="action_my_schedules_0"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu"),
        ]
    )
    return InlineKeyboardMarkup(rows)
