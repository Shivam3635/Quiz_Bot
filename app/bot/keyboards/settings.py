"""Keyboards for batch quiz settings configuration."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.parser.models import QuizSettings


def get_settings_keyboard(settings: QuizSettings) -> InlineKeyboardMarkup:
    """Generate dynamic inline keyboard reflecting current settings."""
    anon_status = "ON ✅" if settings.is_anonymous else "OFF ❌"
    shuffle_status = "ON ✅" if settings.shuffle_options else "OFF ❌"
    expl_status = "ON ✅" if settings.explanation_enabled else "OFF ❌"
    banner_status = "ON ✅" if settings.header_banner_enabled else "OFF ❌"

    if settings.time_limit:
        timer_status = f"{settings.time_limit}s ⏱️"
    else:
        timer_status = "None (No Timer)"

    dest_status = settings.channel_id if settings.channel_id else "This Chat / Default"
    title_display = settings.title[:18] + "..." if settings.title and len(settings.title) > 18 else (settings.title or "None (Tap to set)")
    desc_display = settings.description[:18] + "..." if settings.description and len(settings.description) > 18 else (settings.description or "None (Tap to set)")

    keyboard = [
        [
            InlineKeyboardButton(f"🏷️ Quiz Title: {title_display}", callback_data="prompt_set_title"),
        ],
        [
            InlineKeyboardButton(f"📝 Description: {desc_display}", callback_data="prompt_set_desc"),
        ],
        [
            InlineKeyboardButton(f"📌 Channel Header Banner: {banner_status}", callback_data="toggle_header_banner"),
        ],
        [
            InlineKeyboardButton(f"🕶️ Anonymous: {anon_status}", callback_data="toggle_anonymous"),
            InlineKeyboardButton(f"🔀 Shuffle: {shuffle_status}", callback_data="toggle_shuffle"),
        ],
        [
            InlineKeyboardButton(f"💡 Explanation: {expl_status}", callback_data="toggle_explanation"),
            InlineKeyboardButton(f"⏱️ Timer: {timer_status}", callback_data="cycle_timer"),
        ],
        [
            InlineKeyboardButton(f"📢 Target: {dest_status}", callback_data="set_channel_dest"),
        ],
        [
            InlineKeyboardButton("👀 Preview Quizzes", callback_data="goto_preview"),
            InlineKeyboardButton("🚀 Publish Directly", callback_data="goto_confirm_publish"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="action_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_timer_select_keyboard() -> InlineKeyboardMarkup:
    """Return options for selecting poll timer in seconds."""
    keyboard = [
        [
            InlineKeyboardButton("Off (No limit)", callback_data="set_timer_0"),
            InlineKeyboardButton("15s", callback_data="set_timer_15"),
            InlineKeyboardButton("30s", callback_data="set_timer_30"),
        ],
        [
            InlineKeyboardButton("45s", callback_data="set_timer_45"),
            InlineKeyboardButton("60s", callback_data="set_timer_60"),
            InlineKeyboardButton("120s", callback_data="set_timer_120"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Settings", callback_data="back_to_settings"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_quiz_settings_config_keyboard(settings: QuizSettings, quiz_set_id: int | None = None) -> InlineKeyboardMarkup:
    """Generate dynamic inline keyboard for comprehensive settings configuration."""
    id_tag = str(quiz_set_id) if quiz_set_id is not None else "draft"

    timer_str = f"{settings.time_limit}s ⏱️" if settings.time_limit else "Off (No limit)"
    shuffle_str = "ON ✅" if settings.shuffle_options else "OFF ❌"
    anon_str = "ON (Anonymous) 🕶️" if settings.is_anonymous else "OFF (Public) 👤"
    expl_str = "ON ✅" if settings.explanation_enabled else "OFF ❌"
    banner_str = "ON ✅" if settings.header_banner_enabled else "OFF ❌"
    dest_str = settings.channel_id if settings.channel_id else "This chat (Default)"

    done_btn = (
        InlineKeyboardButton("✅ Done / Back to Quiz", callback_data=f"view_quiz_{quiz_set_id}")
        if quiz_set_id is not None
        else InlineKeyboardButton("✅ Save & Finish", callback_data="finish_creation_settings")
    )

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⏱️ Timer: {timer_str}", callback_data=f"cfg_timer_{id_tag}")],
        [InlineKeyboardButton(f"🔀 Shuffle Options: {shuffle_str}", callback_data=f"cfg_shuffle_{id_tag}")],
        [InlineKeyboardButton(f"🕶️ Mode: {anon_str}", callback_data=f"cfg_anon_{id_tag}")],
        [InlineKeyboardButton(f"💡 Explanations: {expl_str}", callback_data=f"cfg_expl_{id_tag}")],
        [InlineKeyboardButton(f"📢 Header Banner: {banner_str}", callback_data=f"cfg_banner_{id_tag}")],
        [InlineKeyboardButton(f"🎯 Destination: {dest_str}", callback_data=f"cfg_dest_{id_tag}")],
        [done_btn],
    ])

