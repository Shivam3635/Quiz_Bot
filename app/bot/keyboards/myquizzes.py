"""Keyboards for /myquizzes dashboard and re-publishing workflow."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.database.models import QuizSet


def get_my_quizzes_keyboard(
    quiz_sets: list[QuizSet],
    page: int = 0,
    total_count: int = 0,
    per_page: int = 5,
) -> InlineKeyboardMarkup:
    """Generate interactive keyboard listing user's saved quiz sets with pagination."""
    keyboard: list[list[InlineKeyboardButton]] = []

    # Individual quiz set buttons
    for idx, qs in enumerate(quiz_sets, start=page * per_page + 1):
        q_count = len(qs.questions)
        title_snippet = qs.title[:24] + "..." if len(qs.title) > 24 else qs.title
        btn_text = f"{idx}. {title_snippet} ({q_count} Qs)"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_quiz_{qs.id}")])

    # Pagination controls
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"my_quizzes_list_{page - 1}"))

    nav_row.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="myquizzes_noop"))

    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_quizzes_list_{page + 1}"))

    if len(nav_row) > 1:
        keyboard.append(nav_row)

    # Actions row
    keyboard.append([
        InlineKeyboardButton("➕ Create New Quiz", callback_data="action_bulk_create"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="action_main_menu"),
    ])

    return InlineKeyboardMarkup(keyboard)


def get_quiz_details_keyboard(quiz_set_id: int, bot_username: str | None = None) -> InlineKeyboardMarkup:
    """Generate action controls for a specific saved quiz set."""
    keyboard = [
        [
            InlineKeyboardButton("🚀 Publish", callback_data=f"execute_republish_{quiz_set_id}"),
        ],
    ]

    if bot_username:
        keyboard.append([
            InlineKeyboardButton("🎮 Play in Group Battle", url=f"https://t.me/{bot_username}?startgroup=quiz_{quiz_set_id}"),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("🎮 Play in Group Battle", callback_data=f"play_group_prompt_{quiz_set_id}"),
        ])

    keyboard.extend([
        [
            InlineKeyboardButton("⚙️ Quiz Settings", callback_data=f"edit_quiz_settings_{quiz_set_id}"),
        ],
        [
            InlineKeyboardButton("📢 Change Destination", callback_data=f"republish_change_dest_{quiz_set_id}"),
        ],
        [
            InlineKeyboardButton("👀 Preview & Edit Questions", callback_data=f"preview_saved_{quiz_set_id}_0"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_saved_prompt_{quiz_set_id}"),
        ],
        [
            InlineKeyboardButton("🔙 Back to My Quizzes", callback_data="my_quizzes_list_0"),
        ],
    ])
    return InlineKeyboardMarkup(keyboard)


def get_republish_confirm_keyboard(quiz_set_id: int) -> InlineKeyboardMarkup:
    """Generate confirmation controls before triggering re-publication."""
    keyboard = [
        [
            InlineKeyboardButton("🚀 Publish Now", callback_data=f"execute_republish_{quiz_set_id}"),
        ],
        [
            InlineKeyboardButton("📢 Change Target Channel", callback_data=f"republish_change_dest_{quiz_set_id}"),
        ],
        [
            InlineKeyboardButton("🔙 Cancel", callback_data=f"view_quiz_{quiz_set_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_preview_saved_quiz_keyboard(
    quiz_set_id: int, current_idx: int, total_questions: int
) -> InlineKeyboardMarkup:
    """Navigation keyboard for previewing saved quiz questions."""
    keyboard: list[list[InlineKeyboardButton]] = []

    # Navigation buttons
    prev_idx = (current_idx - 1) % total_questions
    next_idx = (current_idx + 1) % total_questions

    keyboard.append([
        InlineKeyboardButton("⬅️ Prev", callback_data=f"preview_saved_{quiz_set_id}_{prev_idx}"),
        InlineKeyboardButton(f"{current_idx + 1} / {total_questions}", callback_data="myquizzes_noop"),
        InlineKeyboardButton("Next ➡️", callback_data=f"preview_saved_{quiz_set_id}_{next_idx}"),
    ])

    keyboard.append([
        InlineKeyboardButton("✏️ Edit This Question", callback_data=f"edit_saved_q_{quiz_set_id}_{current_idx}"),
        InlineKeyboardButton("🗑️ Delete Question", callback_data=f"del_saved_q_{quiz_set_id}_{current_idx}"),
    ])

    keyboard.append([
        InlineKeyboardButton("🚀 Publish This Quiz", callback_data=f"execute_republish_{quiz_set_id}"),
    ])

    keyboard.append([
        InlineKeyboardButton("🔙 Back to Quiz Details", callback_data=f"view_quiz_{quiz_set_id}"),
    ])

    return InlineKeyboardMarkup(keyboard)


def get_delete_confirm_keyboard(quiz_set_id: int) -> InlineKeyboardMarkup:
    """Confirmation keyboard for deleting a saved quiz."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ Yes, Delete Quiz", callback_data=f"confirm_delete_quiz_{quiz_set_id}"),
        ],
        [
            InlineKeyboardButton("🔙 Cancel", callback_data=f"view_quiz_{quiz_set_id}"),
        ],
    ])
