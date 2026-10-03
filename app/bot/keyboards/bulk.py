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


def get_parse_error_keyboard() -> InlineKeyboardMarkup:
    """Return options when parsing a combined quiz fails."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Add More Parts / Fix", callback_data="multipart_continue"),
            ],
            [
                InlineKeyboardButton("🔄 Discard & Start Again", callback_data="multipart_discard"),
                InlineKeyboardButton("❌ Cancel", callback_data="action_cancel"),
            ],
        ]
    )


def get_validation_error_keyboard(
    first_invalid_idx: int,
    total_valid: int,
    total_invalid: int,
    has_oversized: bool = False,
) -> InlineKeyboardMarkup:
    """Return options when questions fail validation, enabling in-place fixing and auto-shortening."""
    rows = []
    if has_oversized:
        rows.append(
            [
                InlineKeyboardButton("✨ Auto Shorten Options", callback_data="auto_shorten_options"),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                f"🛠️ Fix Question {first_invalid_idx} Directly",
                callback_data=f"fix_invalid_q_{first_invalid_idx - 1}",
            ),
            InlineKeyboardButton("👀 Review All (Preview)", callback_data="goto_preview"),
        ]
    )

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


def get_shortened_option_review_keyboard(
    q_idx: int,
    opt_idx: int,
    can_keep_original: bool = False,
    remaining_count: int = 1,
) -> InlineKeyboardMarkup:
    """Return controls for reviewing a proposed shortened option."""
    row1 = [InlineKeyboardButton("✅ Accept", callback_data=f"accept_shortened_{q_idx}_{opt_idx}")]
    if remaining_count > 1:
        row1.append(InlineKeyboardButton(f"✅ Accept All ({remaining_count})", callback_data="accept_all_shortened"))

    row2 = [InlineKeyboardButton("✏️ Edit Manually", callback_data=f"edit_shortened_{q_idx}_{opt_idx}")]
    if can_keep_original:
        row2.append(InlineKeyboardButton("↩️ Keep Original", callback_data=f"keep_orig_shortened_{q_idx}_{opt_idx}"))

    return InlineKeyboardMarkup(
        [
            row1,
            row2,
            [InlineKeyboardButton("❌ Cancel", callback_data="action_cancel")],
        ]
    )


