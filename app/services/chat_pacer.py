"""Centralized per-chat message pacing controller for Telegram publishing."""

import asyncio
import time
from typing import Callable, Optional
from app.config.settings import get_settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class ChatPacer:
    """
    Centralized per-chat message pacing controller.
    Ensures that successive outgoing Telegram API messages to the same destination chat
    are spaced by at least `pacing_interval` seconds (default 3.0s),
    measuring from the actual completion time of the previous message.
    """

    def __init__(
        self,
        pacing_interval: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        if pacing_interval is not None:
            self.pacing_interval = float(pacing_interval)
        else:
            settings = get_settings()
            self.pacing_interval = float(
                getattr(
                    settings,
                    "DEFAULT_MESSAGE_PACING_INTERVAL",
                    getattr(settings, "DEFAULT_DELAY_BETWEEN_POSTS", 3.0),
                )
            )
        self.clock: Callable[[], float] = clock or time.monotonic
        self._next_allowed_send_time: dict[str, float] = {}
        self._mutex: asyncio.Lock = asyncio.Lock()

    @staticmethod
    def normalize_chat_id(chat_id: str | int) -> str:
        """
        Normalize chat identifier to consistent string representation:
        - Numeric IDs (int or numeric string) normalized to canonical str(int).
        - Usernames stripped of leading '@' and converted to lowercase.
        """
        s = str(chat_id).strip()
        try:
            return str(int(s))
        except ValueError:
            stripped = s.lstrip("@")
            try:
                return str(int(stripped))
            except ValueError:
                return stripped.lower()

    async def wait_for_turn(self, chat_id: str | int, interval: Optional[float] = None) -> float:
        """
        Wait asynchronously until the next outgoing message is permitted for this chat.
        Returns the duration waited in seconds.
        """
        key = self.normalize_chat_id(chat_id)
        now = self.clock()

        async with self._mutex:
            next_allowed = self._next_allowed_send_time.get(key, 0.0)
            wait_time = max(0.0, next_allowed - now)

        if wait_time > 0:
            logger.debug("Pacing chat %s: pausing for %.2fs", key, wait_time)
            await asyncio.sleep(wait_time)

        return wait_time

    def record_send(self, chat_id: str | int, interval: Optional[float] = None) -> None:
        """
        Record that a message was successfully sent to this destination chat.
        Schedules the next permissible send time at completion_time + interval.
        """
        key = self.normalize_chat_id(chat_id)
        target_interval = float(interval if interval is not None else self.pacing_interval)
        now = self.clock()
        self._next_allowed_send_time[key] = now + target_interval
        logger.debug(
            "Chat %s send recorded: next send allowed at %.2f (+%.2fs)",
            key,
            self._next_allowed_send_time[key],
            target_interval,
        )

    def record_flood_wait(self, chat_id: str | int, wait_seconds: float) -> None:
        """
        Record that Telegram returned RetryAfter for this chat.
        Updates the next permissible send time to cover the required flood wait.
        """
        key = self.normalize_chat_id(chat_id)
        now = self.clock()
        required_time = now + float(wait_seconds)
        self._next_allowed_send_time[key] = max(
            self._next_allowed_send_time.get(key, 0.0),
            required_time,
        )
        logger.warning(
            "Chat %s flood-waited: next send scheduled after %.2fs",
            key,
            wait_seconds,
        )

    def clear_flood_wait(self, chat_id: str | int) -> None:
        """Clear pending flood-wait constraint once the wait duration has elapsed."""
        key = self.normalize_chat_id(chat_id)
        self._next_allowed_send_time[key] = self.clock()

    def reset_chat(self, chat_id: str | int) -> None:
        """Reset pacing state for a specific chat."""
        key = self.normalize_chat_id(chat_id)
        self._next_allowed_send_time.pop(key, None)

    def reset_all(self) -> None:
        """Reset pacing state for all chats (useful for testing and process lifecycle)."""
        self._next_allowed_send_time.clear()

    def get_next_send_time(self, chat_id: str | int) -> float:
        """Get the monotonic timestamp when next send is permitted."""
        key = self.normalize_chat_id(chat_id)
        return self._next_allowed_send_time.get(key, 0.0)


# Process-wide default instance
default_chat_pacer = ChatPacer()
