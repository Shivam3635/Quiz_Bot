"""Keyboards package."""

from app.bot.keyboards.main import get_cancel_keyboard, get_main_menu_keyboard
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

__all__ = [
    "get_automation_menu_keyboard",
    "get_cancel_keyboard",
    "get_main_menu_keyboard",
    "get_my_schedules_keyboard",
    "get_schedule_confirm_keyboard",
    "get_schedule_date_keyboard",
    "get_schedule_details_keyboard",
    "get_schedule_groups_keyboard",
    "get_schedule_quizzes_keyboard",
    "get_schedule_time_keyboard",
    "get_schedule_timezone_keyboard",
]
