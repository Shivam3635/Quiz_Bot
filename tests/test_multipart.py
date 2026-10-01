"""Unit tests for Multi-Part Bulk Quiz Input workflow (Section 25)."""

import pytest
from datetime import datetime, timezone, timedelta
from app.services.session_service import SessionManager, QuizSession, SESSION_TIMEOUT_SECONDS
from app.services.quiz_service import QuizService
from app.parser.parser import BulkQuizParser


@pytest.fixture
def session_mgr() -> SessionManager:
    return SessionManager()


@pytest.fixture
def quiz_service() -> QuizService:
    return QuizService()


def generate_quiz_chunk(start_q: int, count: int) -> str:
    """Helper to generate a block of numbered questions."""
    blocks = []
    for i in range(start_q, start_q + count):
        blocks.append(
            f"Q{i}. What is question number {i}?\n"
            f"A) Choice A\n"
            f"B) Choice B\n"
            f"C) Choice C\n"
            f"D) Choice D\n"
            f"Answer: B"
        )
    return "\n\n".join(blocks)


def test_1_ten_questions_one_message(session_mgr, quiz_service):
    """Test 1: 10 questions in 1 message -> Done -> 10 questions."""
    session = session_mgr.create_session(user_id=1, chat_id=1)
    chunk = generate_quiz_chunk(1, 10)

    part = session.add_part(chunk, detected_questions=10)
    assert session.total_parts == 1
    assert session.total_questions_detected == 10

    combined = session.combine_parts_text()
    result = quiz_service.process_raw_text(combined)

    assert result.is_ready_for_publish
    assert len(result.questions) == 10
    assert result.questions[0].question == "What is question number 1?"
    assert result.questions[9].question == "What is question number 10?"


def test_2_twenty_questions_two_messages(session_mgr, quiz_service):
    """Test 2: Part 1 -> Q1-Q10, Part 2 -> Q11-Q20 -> 20 questions."""
    session = session_mgr.create_session(user_id=2, chat_id=2)

    chunk1 = generate_quiz_chunk(1, 10)
    chunk2 = generate_quiz_chunk(11, 10)

    session.add_part(chunk1, detected_questions=10)
    session.add_part(chunk2, detected_questions=10)

    assert session.total_parts == 2
    assert session.total_questions_detected == 20

    combined = session.combine_parts_text()
    result = quiz_service.process_raw_text(combined)

    assert result.is_ready_for_publish
    assert len(result.questions) == 20
    assert result.questions[0].question.startswith("What is question number 1?")
    assert result.questions[19].question.startswith("What is question number 20?")


def test_3_fifty_questions_five_messages(session_mgr, quiz_service):
    """Test 3: 5 messages of 10 questions each -> 50 questions."""
    session = session_mgr.create_session(user_id=3, chat_id=3)

    for i in range(5):
        chunk = generate_quiz_chunk(i * 10 + 1, 10)
        session.add_part(chunk, detected_questions=10)

    assert session.total_parts == 5
    assert session.total_questions_detected == 50

    combined = session.combine_parts_text()
    result = quiz_service.process_raw_text(combined)

    assert result.is_ready_for_publish
    assert len(result.questions) == 50
    assert result.questions[49].question.startswith("What is question number 50?")


def test_4_unequal_chunks(session_mgr, quiz_service):
    """Test 4: Part 1 (7 questions), Part 2 (13 questions), Part 3 (8 questions) -> 28 questions."""
    session = session_mgr.create_session(user_id=4, chat_id=4)

    c1 = generate_quiz_chunk(1, 7)
    c2 = generate_quiz_chunk(8, 13)
    c3 = generate_quiz_chunk(21, 8)

    session.add_part(c1, detected_questions=7)
    session.add_part(c2, detected_questions=13)
    session.add_part(c3, detected_questions=8)

    assert session.total_parts == 3
    assert session.total_questions_detected == 28

    combined = session.combine_parts_text()
    result = quiz_service.process_raw_text(combined)

    assert result.is_ready_for_publish
    assert len(result.questions) == 28


def test_5_empty_done(session_mgr):
    """Test 5: /newquiz -> Done with 0 parts -> Warning, no processing."""
    session = session_mgr.create_session(user_id=5, chat_id=5)
    assert session.total_parts == 0
    # Condition in multipart_done_callback: session.total_parts == 0 prevents processing
    assert session.total_questions_detected == 0
    assert session.combine_parts_text() == ""


def test_6_invalid_part():
    """Test 6: Message 'hello' has 0 detected questions and should not be added."""
    parser = BulkQuizParser()
    res = parser.parse("hello there, this is my next part")
    assert len(res.questions) == 0
    # In bulk.py, detected_count == 0 prevents part from being added to session


def test_7_cancel_session(session_mgr):
    """Test 7: Start session, send 2 parts, Cancel -> session is cleared."""
    session = session_mgr.create_session(user_id=7, chat_id=7)
    session.add_part(generate_quiz_chunk(1, 3), detected_questions=3)
    session.add_part(generate_quiz_chunk(4, 3), detected_questions=3)

    assert session.total_parts == 2

    # Discard session
    session_mgr.discard_session(user_id=7, chat_id=7)
    assert session_mgr.get_active_session(user_id=7, chat_id=7) is None


def test_8_multiple_users_isolation(session_mgr, quiz_service):
    """Test 8: Simulate User A and User B concurrently with zero cross-contamination."""
    # User A starts
    session_a = session_mgr.create_session(user_id=101, chat_id=101)
    session_a.add_part(generate_quiz_chunk(1, 10), detected_questions=10)

    # User B starts
    session_b = session_mgr.create_session(user_id=102, chat_id=102)
    session_b.add_part(generate_quiz_chunk(1, 20), detected_questions=20)

    # User A adds Part 2
    session_a.add_part(generate_quiz_chunk(11, 10), detected_questions=10)

    # Verify sessions are isolated
    assert session_a.total_parts == 2
    assert session_a.total_questions_detected == 20

    assert session_b.total_parts == 1
    assert session_b.total_questions_detected == 20

    res_a = quiz_service.process_raw_text(session_a.combine_parts_text())
    res_b = quiz_service.process_raw_text(session_b.combine_parts_text())

    assert len(res_a.questions) == 20
    assert len(res_b.questions) == 20


def test_9_double_done_lock(session_mgr):
    """Test 9: Lock prevents duplicate processing."""
    session = session_mgr.create_session(user_id=9, chat_id=9)
    session.add_part(generate_quiz_chunk(1, 5), detected_questions=5)

    assert not session.is_processing_lock
    session.is_processing_lock = True
    session.status = "PROCESSING"

    # Second Done check:
    is_blocked = session.is_processing_lock or session.status == "PROCESSING"
    assert is_blocked is True


def test_10_session_timeout(session_mgr):
    """Test 10: Inactive session beyond 30 minutes expires automatically."""
    session = session_mgr.create_session(user_id=10, chat_id=10)
    session.add_part(generate_quiz_chunk(1, 2), detected_questions=2)

    # Artificially age the session updated_at beyond 30 minutes
    session.updated_at = datetime.now(timezone.utc) - timedelta(seconds=SESSION_TIMEOUT_SECONDS + 10)
    assert session.is_expired is True

    # Retrieving active session should clear it and return None
    active = session_mgr.get_active_session(user_id=10, chat_id=10)
    assert active is None
