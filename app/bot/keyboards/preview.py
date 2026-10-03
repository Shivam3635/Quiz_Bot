"""Keyboards for quiz preview pagination, editing, and confirmation."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.parser.models import QuizQuestion


def get_preview_keyboard(
    current_index: int,
    total_questions: int,
    invalid_indices: list[int] | None = None,
    saved_quiz_set_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Generate pagination and navigation controls for previewing questions."""
    nav_row = []

    # Previous button (disabled or hidden on first page)
    if current_index > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"preview_nav_{current_index - 1}"))
    else:
        nav_row.append(InlineKeyboardButton("⏮️ (First)", callback_data="preview_noop"))

    # Page indicator
    nav_row.append(InlineKeyboardButton(f"{current_index + 1} / {total_questions}", callback_data="preview_noop"))

    # Next button
    if current_index < total_questions - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"preview_nav_{current_index + 1}"))
    else:
        nav_row.append(InlineKeyboardButton("⏭️ (Last)", callback_data="preview_noop"))

    keyboard = [nav_row]

    # If there are invalid questions, provide a jump-to-issue button
    if invalid_indices:
        keyboard.append([
            InlineKeyboardButton(f"⚠️ Jump to Issue ({len(invalid_indices)} need attention)", callback_data="jump_next_invalid")
        ])

    if saved_quiz_set_id:
        keyboard.extend([
            [
                InlineKeyboardButton("✏️ Edit This Question", callback_data="edit_current_question"),
                InlineKeyboardButton("🗑️ Delete", callback_data="delete_current_question"),
            ],
            [
                InlineKeyboardButton("⚙️ Quiz Settings", callback_data=f"edit_quiz_settings_{saved_quiz_set_id}"),
                InlineKeyboardButton("🚀 Publish", callback_data=f"execute_republish_{saved_quiz_set_id}"),
            ],
            [
                InlineKeyboardButton("🔙 Back to Quiz Details", callback_data=f"view_quiz_{saved_quiz_set_id}"),
            ],
        ])
    else:
        keyboard.extend([
            [
                InlineKeyboardButton("✏️ Edit This Question", callback_data="edit_current_question"),
                InlineKeyboardButton("🗑️ Delete", callback_data="delete_current_question"),
            ],
            [
                InlineKeyboardButton("⚙️ Edit Settings", callback_data="goto_settings"),
                InlineKeyboardButton("🚀 Publish All", callback_data="goto_confirm_publish"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="action_cancel"),
            ],
        ])
    return InlineKeyboardMarkup(keyboard)


def get_edit_question_keyboard(
    q: QuizQuestion, current_index: int, saved_quiz_set_id: int | None = None
) -> InlineKeyboardMarkup:
    """Generate component-level editing options for a single question with validation indicators."""
    # Quick 1-click correct answer switch
    ans_buttons = []
    for opt_idx, _ in enumerate(q.options):
        letter = chr(ord("A") + opt_idx)
        is_curr = "✅ " if opt_idx == q.correct_option else ""
        ans_buttons.append(
            InlineKeyboardButton(f"{is_curr}{letter}", callback_data=f"set_correct_ans_{opt_idx}")
        )

    # Option edit buttons (2 per row) with length alerts
    opt_edit_rows = []
    current_opt_row = []
    for opt_idx, opt_text in enumerate(q.options):
        letter = chr(ord("A") + opt_idx)
        opt_len = len(opt_text.strip())
        warn_tag = f" ⚠️({opt_len})" if opt_len > 100 or opt_len == 0 else ""
        current_opt_row.append(
            InlineKeyboardButton(f"✏️ Opt {letter}{warn_tag}", callback_data=f"edit_opt_{opt_idx}")
        )
        if len(current_opt_row) == 2:
            opt_edit_rows.append(current_opt_row)
            current_opt_row = []
    if current_opt_row:
        opt_edit_rows.append(current_opt_row)

    # Auto-shorten buttons for any oversized options
    auto_shorten_rows = []
    for opt_idx, opt_text in enumerate(q.options):
        if len(opt_text.strip()) > 100:
            letter = chr(ord("A") + opt_idx)
            auto_shorten_rows.append(
                [
                    InlineKeyboardButton(
                        f"✨ Auto-Shorten Opt {letter} ({len(opt_text.strip())}/100)",
                        callback_data=f"auto_shorten_single_{current_index}_{opt_idx}",
                    )
                ]
            )

    q_len = len(q.question.strip())
    q_warn = f" ⚠️ ({q_len}/300)" if q_len > 300 or q_len == 0 else ""

    expl_len = len(q.explanation) if q.explanation else 0
    expl_warn = f" ⚠️ ({expl_len}/200)" if expl_len > 200 else ""

    back_row = [InlineKeyboardButton("🔙 Back to Preview", callback_data="goto_preview")]
    if saved_quiz_set_id:
        back_row.append(InlineKeyboardButton("💾 Quiz Details", callback_data=f"view_quiz_{saved_quiz_set_id}"))

    keyboard = [
        [InlineKeyboardButton("👇 Tap to change Correct Answer:", callback_data="preview_noop")],
        ans_buttons,
        [InlineKeyboardButton(f"✏️ Edit Question Text{q_warn}", callback_data="edit_q_text")],
        *opt_edit_rows,
        *auto_shorten_rows,
        [
            InlineKeyboardButton(f"💡 Edit Explanation{expl_warn}", callback_data="edit_q_expl"),
            InlineKeyboardButton("📋 Copyable Format", callback_data="show_copyable_q"),
        ],
        back_row,
    ]
    return InlineKeyboardMarkup(keyboard)


def get_delete_confirm_keyboard(current_index: int) -> InlineKeyboardMarkup:
    """Generate confirmation to delete a question."""
    keyboard = [
        [
            InlineKeyboardButton("🗑️ Yes, Delete", callback_data=f"confirm_delete_{current_index}"),
            InlineKeyboardButton("🔙 Cancel", callback_data="goto_preview"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_publish_confirm_keyboard(total_questions: int) -> InlineKeyboardMarkup:
    """Generate confirmation keyboard before initiating bulk publishing."""
    keyboard = [
        [
            InlineKeyboardButton(f"🚀 Publish {total_questions} Quizzes", callback_data="confirm_publish_now"),
        ],
        [
            InlineKeyboardButton("👀 Back to Preview", callback_data="goto_preview"),
            InlineKeyboardButton("⚙️ Settings", callback_data="goto_settings"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="action_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_retry_keyboard() -> InlineKeyboardMarkup:
    """Generate retry options for failed questions."""
    keyboard = [
        [
            InlineKeyboardButton("🔄 Retry Failed Quizzes", callback_data="retry_failed_quizzes"),
        ],
        [
            InlineKeyboardButton("🏠 Back to Main Menu", callback_data="action_main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)
