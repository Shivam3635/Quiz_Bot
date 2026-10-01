"""Unit tests for Live Group Quiz Mode and Real-Time Leaderboards."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Bot, Message, Poll
from telegram.constants import ParseMode, PollType

from app.database.database import Base, SessionLocal, engine
from app.database.models import User, QuizSet, Question, Option, QuizSettingsRecord
from app.database.repositories import record_battle_result
from app.parser.models import QuizQuestion
from app.services.game_service import (
    GameSession,
    ParticipantScore,
    QuizGameManager,
    get_game_manager,
    run_game_loop,
)
from app.bot.keyboards.game import (
    get_game_lobby_keyboard,
    get_game_finished_keyboard,
    get_select_quiz_for_game_keyboard,
)


@pytest.fixture(autouse=True)
def init_test_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def sample_game_questions() -> list[QuizQuestion]:
    return [
        QuizQuestion(
            question="What is the capital of France?",
            options=["Berlin", "Madrid", "Paris", "Rome"],
            correct_option=2,
            explanation="Paris is the capital of France.",
            question_number=1,
        ),
        QuizQuestion(
            question="What is 5 * 6?",
            options=["30", "25", "35"],
            correct_option=0,
            explanation="5 times 6 is 30.",
            question_number=2,
        ),
    ]


def test_participant_score_ranking():
    """Verify that participants are ranked by score (descending) and time (ascending)."""
    p1 = ParticipantScore(user_id=1, full_name="Alice", score=5, total_time=20.5)
    p2 = ParticipantScore(user_id=2, full_name="Bob", score=8, total_time=30.0)
    p3 = ParticipantScore(user_id=3, full_name="Charlie", score=8, total_time=25.0)  # Same score as Bob, but faster

    session = GameSession(
        chat_id=100,
        chat_title="Test Group",
        quiz_set_id=1,
        title="Maths Test",
        description="Quick test",
        questions=[],
        scores={1: p1, 2: p2, 3: p3},
    )

    ranked = session.get_sorted_leaderboard()
    assert [p.user_id for p in ranked] == [3, 2, 1]  # Charlie (8, 25s), Bob (8, 30s), Alice (5, 20.5s)


def test_game_session_record_answer(sample_game_questions):
    """Verify recording of correct and incorrect answers with response times."""
    session = GameSession(
        chat_id=100,
        chat_title="Study Group",
        quiz_set_id=1,
        title="GK Quiz",
        description=None,
        questions=sample_game_questions,
        status="running",
        question_start_time=1000.0,
    )

    # Correct answer for Q1 (correct_option=2)
    session.current_question_idx = 0
    is_correct = session.record_answer(
        user_id=10,
        full_name="Player 1",
        username="player1",
        selected_option=2,
        correct_option=2,
    )
    assert is_correct is True
    assert session.scores[10].score == 1
    assert 10 in session.round_correct
    assert 10 in session.round_all_answers

    # Incorrect answer for Q1
    is_wrong = session.record_answer(
        user_id=20,
        full_name="Player 2",
        username=None,
        selected_option=1,
        correct_option=2,
    )
    assert is_wrong is False
    assert session.scores[20].score == 0
    assert 20 not in session.round_correct
    assert 20 in session.round_all_answers


def test_game_manager_lifecycle(sample_game_questions):
    """Verify Game manager creation, lookup, and cleanup."""
    manager = QuizGameManager()
    session = manager.create_game(
        chat_id=-100123456,
        chat_title="My Study Circle",
        quiz_set_id=5,
        title="Physics 101",
        description="Mechanics",
        questions=sample_game_questions,
        time_limit=20,
        initiator_id=99,
        initiator_name="Admin",
    )

    assert session.chat_id == -100123456
    assert session.status == "lobby"
    assert session.time_limit == 20
    assert manager.get_game(-100123456) is session

    # Map poll
    manager.poll_to_chat["poll_abc"] = -100123456
    assert manager.get_game_by_poll("poll_abc") is session

    # Stop game
    stopped = manager.stop_game(-100123456)
    assert stopped.status == "stopped"
    assert manager.get_game(-100123456) is None
    assert manager.get_game_by_poll("poll_abc") is None


def test_lobby_and_leaderboard_formatting(sample_game_questions):
    """Verify text rendering for both lobby and podium leaderboard."""
    session = GameSession(
        chat_id=100,
        chat_title="Group",
        quiz_set_id=1,
        title="World Capitals",
        description="Geography quiz",
        questions=sample_game_questions,
        initiator_name="HostUser",
        status="lobby",
    )
    session.add_participant(1, "Alice", "alice_tg")
    session.add_participant(2, "Bob")

    lobby_text = session.format_lobby_text()
    assert "World Capitals" in lobby_text
    assert "Geography quiz" in lobby_text
    assert "@alice_tg" in lobby_text
    assert "Bob" in lobby_text
    assert "HostUser" in lobby_text

    # Record some scores for leaderboard test
    session.scores[1].score = 2
    session.scores[1].total_time = 12.4
    session.scores[2].score = 1
    session.scores[2].total_time = 15.0

    board = session.format_leaderboard_text()
    assert "Final Leaderboard" in board
    assert "🥇" in board
    assert "🥈" in board
    assert "@alice_tg" in board
    assert "2/2" in board
    assert "Winner:" in board


@pytest.mark.asyncio
async def test_run_game_loop_full_cycle(sample_game_questions):
    """Verify game loop executes polls, stops them, and posts final leaderboard."""
    mock_bot = MagicMock(spec=Bot)

    # Mock send_poll
    mock_poll = MagicMock(spec=Poll)
    mock_poll.id = "mock_poll_123"
    mock_msg = MagicMock(spec=Message)
    mock_msg.message_id = 999
    mock_msg.poll = mock_poll

    mock_bot.send_poll = AsyncMock(return_value=mock_msg)
    mock_bot.stop_poll = AsyncMock()
    mock_bot.send_message = AsyncMock()

    session = GameSession(
        chat_id=-100999,
        chat_title="Battle Arena",
        quiz_set_id=1,
        title="Quick Quiz",
        description=None,
        questions=sample_game_questions,
        time_limit=5,  # 5s
    )
    session.add_participant(user_id=1, full_name="Player 1")

    # Shorten sleep in loop by simulating answers
    async def simulate_answer():
        await asyncio.sleep(0.1)
        session.record_answer(
            user_id=1,
            full_name="Player 1",
            username="player1",
            selected_option=sample_game_questions[0].correct_option,
            correct_option=sample_game_questions[0].correct_option,
        )

    asyncio.create_task(simulate_answer())

    await run_game_loop(mock_bot, session)

    assert session.status == "finished"
    assert mock_bot.send_poll.call_count == 2  # 2 questions
    assert mock_bot.stop_poll.call_count == 2
    assert mock_bot.send_message.call_count == 1  # final leaderboard only, no intermediate chat spam

    # Verify is_anonymous was False in send_poll calls
    for call in mock_bot.send_poll.call_args_list:
        assert call.kwargs["is_anonymous"] is False
        assert call.kwargs["type"] == PollType.QUIZ


def test_record_battle_result_in_db():
    """Verify persisting completed battle results into database."""
    with SessionLocal() as db:
        # Create a test user and quiz set first
        from app.database.repositories import get_or_create_user
        user = get_or_create_user(db, telegram_id=987654, username="test_host")

        qs = QuizSet(user_id=user.id, title="DB Battle Quiz")
        db.add(qs)
        db.commit()

        battle = record_battle_result(
            db=db,
            quiz_set_id=qs.id,
            chat_id=-100888999,
            chat_title="Study Group Super",
            winner_name="WinnerPro",
            winner_score=10,
            total_participants=4,
        )

        assert battle.id is not None
        assert battle.quiz_set_id == qs.id
        assert battle.chat_id == "-100888999"
        assert battle.winner_name == "WinnerPro"
        assert battle.winner_score == 10
        assert battle.total_participants == 4


def test_game_keyboards():
    """Verify game lobby, finish, and selection keyboards."""
    lobby_kb = get_game_lobby_keyboard(12)
    cbs = [btn.callback_data for row in lobby_kb.inline_keyboard for btn in row]
    assert cbs == ["game_join_12"]
    assert lobby_kb.inline_keyboard[0][0].text == "🙋 I am ready!"

    fin_kb = get_game_finished_keyboard(12)
    fin_cbs = [btn.callback_data for row in fin_kb.inline_keyboard for btn in row]
    assert len(fin_cbs) == 0  # Play Again removed

    qs = QuizSet(id=7, title="Sample Quiz", questions=[])
    sel_kb = get_select_quiz_for_game_keyboard([qs])
    sel_cbs = [btn.callback_data for row in sel_kb.inline_keyboard for btn in row]
    assert "game_launch_7" in sel_cbs
    assert "game_cancel_selection" in sel_cbs
