"""Keyboards for multi-part quiz creation workflow."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_title_input_keyboard() -> InlineKeyboardMarkup:
    """Return controls for quiz title prompt (Skip or Cancel)."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏭️ Skip Title", callback_data="skip_quiz_title")],
            [InlineKeyboardButton("❌ Cancel", callback_data="action_cancel")],
        ]
    )


def get_description_input_keyboard() -> InlineKeyboardMarkup:
    """Return controls for quiz description prompt (Skip or Cancel)."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏭️ Skip Description", callback_data="skip_quiz_desc")],
            [InlineKeyboardButton("❌ Cancel", callback_data="action_cancel")],
        ]
    )


def get_multipart_input_keyboard() -> InlineKeyboardMarkup:
    """Return controls for multi-part question collection."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Done", callback_data="multipart_done"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="action_cancel"),
            ],
        ]
    )


def get_session_conflict_keyboard() -> InlineKeyboardMarkup:
    """Return options when an existing multi-part session is active."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("▶️ Continue Current Quiz", callback_data="multipart_continue"),
            ],
            [
                InlineKeyboardButton("🗑️ Discard & Start New", callback_data="multipart_discard"),
            ],
        ]
    )


def get_parse_error_keyboard(
    first_error_q: int | None = None,
    valid_count: int = 0,
) -> InlineKeyboardMarkup:
    """Return options when parsing a combined quiz fails."""
    rows = []
    if first_error_q is not None and valid_count > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    f"🗑️ Drop Question {first_error_q} (Keep {valid_count} Valid)",
                    callback_data=f"drop_broken_q_{first_error_q}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("➕ Send More Parts", callback_data="multipart_continue"),
            InlineKeyboardButton("🔄 Discard & Start Again", callback_data="multipart_discard"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("❌ Cancel", callback_data="action_cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_validation_error_keyboard(
    first_invalid_idx: int,
    total_valid: int,
    total_invalid: int,
) -> InlineKeyboardMarkup:
    """Return options when questions fail validation, enabling in-place fixing."""
    rows = [
        [
            InlineKeyboardButton(
                f"🛠️ Fix Question {first_invalid_idx} Directly",
                callback_data=f"fix_invalid_q_{first_invalid_idx - 1}",
            ),
            InlineKeyboardButton("👀 Review All (Preview)", callback_data="goto_preview"),
        ],
    ]

    if total_valid > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    f"🗑️ Drop {total_invalid} Invalid (Keep {total_valid} Valid)",
                    callback_data="drop_invalid_questions",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton("➕ Add More Parts", callback_data="multipart_continue"),
            InlineKeyboardButton("❌ Cancel", callback_data="action_cancel"),
        ]
    )

    return InlineKeyboardMarkup(rows)

