"""Session management for multi-part bulk quiz creation."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes inactivity timeout


@dataclass
class QuizPart:
    """Represents a single message part in a multi-part quiz session."""

    part_number: int
    raw_text: str
    detected_questions: int
    character_count: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class QuizSession:
    """Represents an active multi-part quiz creation session."""

    session_id: str
    user_id: int
    chat_id: int
    parts: list[QuizPart] = field(default_factory=list)
    status: str = "WAITING_FOR_INPUT"  # WAITING_FOR_INPUT, PROCESSING, PREVIEW, CANCELLED, COMPLETED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_processing_lock: bool = False

    @property
    def total_parts(self) -> int:
        """Total number of messages collected."""
        return len(self.parts)

    @property
    def total_questions_detected(self) -> int:
        """Estimated total questions across all parts."""
        return sum(p.detected_questions for p in self.parts)

    @property
    def total_characters(self) -> int:
        """Total characters collected."""
        return sum(p.character_count for p in self.parts)

    @property
    def is_expired(self) -> bool:
        """Return True if session has been inactive for longer than timeout."""
        elapsed = (datetime.now(timezone.utc) - self.updated_at).total_seconds()
        return elapsed > SESSION_TIMEOUT_SECONDS

    def add_part(self, raw_text: str, detected_questions: int) -> QuizPart:
        """Append a new message part to the session maintaining strict sequential order."""
        part_num = len(self.parts) + 1
        part = QuizPart(
            part_number=part_num,
            raw_text=raw_text.strip(),
            detected_questions=detected_questions,
            character_count=len(raw_text),
        )
        self.parts.append(part)
        self.updated_at = datetime.now(timezone.utc)
        return part

    def combine_parts_text(self) -> str:
        """Combine all collected parts sequentially with double-newlines."""
        return "\n\n".join(p.raw_text for p in self.parts)

    def touch(self) -> None:
        """Update last active timestamp."""
        self.updated_at = datetime.now(timezone.utc)


class SessionManager:
    """Manages isolated user/chat quiz sessions with concurrency and timeout safeguards."""

    def __init__(self):
        # Key: (user_id, chat_id) -> QuizSession
        self._sessions: dict[tuple[int, int], QuizSession] = {}

    def get_active_session(self, user_id: int, chat_id: int) -> Optional[QuizSession]:
        """Retrieve active session if not expired."""
        key = (user_id, chat_id)
        session = self._sessions.get(key)
        if not session:
            return None

        if session.is_expired:
            session.status = "CANCELLED"
            del self._sessions[key]
            return None

        if session.status in ("CANCELLED", "COMPLETED"):
            del self._sessions[key]
            return None

        session.touch()
        return session

    def create_session(self, user_id: int, chat_id: int) -> QuizSession:
        """Create a fresh quiz creation session for the user/chat."""
        key = (user_id, chat_id)
        session = QuizSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            chat_id=chat_id,
            status="WAITING_FOR_INPUT",
        )
        self._sessions[key] = session
        return session

    def discard_session(self, user_id: int, chat_id: int) -> bool:
        """Cancel and remove the session."""
        key = (user_id, chat_id)
        if key in self._sessions:
            self._sessions[key].status = "CANCELLED"
            del self._sessions[key]
            return True
        return False
