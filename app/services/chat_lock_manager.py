"""In-memory per-chat locking for single-process publishing coordination."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class ChatLockManager:
    """
    Coordinates asynchronous per-chat locks in memory.
    Serializes concurrent publishing operations targeting the same destination chat
    while allowing different chats to publish concurrently.

    NOTE: This lock coordinates operations strictly within the current Python process
    (e.g. single Render instance) and is not a distributed multi-worker lock.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._ref_counts: dict[str, int] = {}
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

    @asynccontextmanager
    async def lock_chat(self, chat_id: str | int) -> AsyncIterator[asyncio.Lock]:
        """
        Asynchronously acquire the lock for a specific chat.
        Tracks active waiters and cleans up unused locks to prevent memory leaks.
        """
        key = self.normalize_chat_id(chat_id)

        async with self._mutex:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
                self._ref_counts[key] = 0
            self._ref_counts[key] += 1
            chat_lock = self._locks[key]

        acquired = False
        try:
            # Asynchronously acquire chat lock without blocking the event loop
            await chat_lock.acquire()
            acquired = True
            logger.debug("Acquired publishing lock for chat %s", key)
            yield chat_lock
        finally:
            if acquired and chat_lock.locked():
                chat_lock.release()
                logger.debug("Released publishing lock for chat %s", key)

            async with self._mutex:
                self._ref_counts[key] -= 1
                if self._ref_counts[key] <= 0 and not chat_lock.locked():
                    self._locks.pop(key, None)
                    self._ref_counts.pop(key, None)
                    logger.debug("Cleaned up unused lock entry for chat %s", key)

    def is_locked(self, chat_id: str | int) -> bool:
        """Check if a chat is currently locked (useful for testing/diagnostics)."""
        key = self.normalize_chat_id(chat_id)
        lock = self._locks.get(key)
        return lock.locked() if lock else False

    def get_waiter_count(self, chat_id: str | int) -> int:
        """Return total active holders and waiters for a chat."""
        key = self.normalize_chat_id(chat_id)
        return self._ref_counts.get(key, 0)


# Process-wide default instance
default_chat_lock_manager = ChatLockManager()

