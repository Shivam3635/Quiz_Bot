"""FSM States for QuizBotPro Telegram Bot."""

from enum import IntEnum, auto


class QuizCreationState(IntEnum):
    """Conversation states during bulk quiz creation flow."""

    WAITING_FOR_TITLE = auto()
    WAITING_FOR_DESCRIPTION = auto()
    WAITING_FOR_BULK_INPUT = auto()
    ANALYZING_INPUT = auto()
    CONFIGURING_SETTINGS = auto()
    PREVIEWING = auto()
    CONFIRMING_PUBLISH = auto()
    PUBLISHING = auto()
    EDITING_QUESTION = auto()


class QuizScheduleState(IntEnum):
    """Conversation states during single quiz scheduling flow."""

    SELECTING_QUIZ = auto()
    SELECTING_GROUP = auto()
    SELECTING_DATE = auto()
    SELECTING_TIME = auto()
    SELECTING_TIMEZONE = auto()
    CONFIRMING_SCHEDULE = auto()
