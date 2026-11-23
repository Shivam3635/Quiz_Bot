"""Comprehensive automated tests for Phase 1: Single Quiz Scheduling."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from telegram import Bot, CallbackQuery, Message, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from app.bot.handlers.schedule import (
    confirm_schedule_callback,
    pick_date_callback,
    pick_quiz_callback,
    pick_time_callback,
    receive_group_text_message,
    start_schedule_flow,
)
from app.bot.keyboards.schedule import (
    get_automation_menu_keyboard,
    get_schedule_date_keyboard,
    get_schedule_time_keyboard,
    get_schedule_timezone_keyboard,
)
from app.bot.states import QuizScheduleState
from app.database.database import Base, SessionLocal, engine
from app.database.models import QuizSchedule
from app.database.repositories import (
    cancel_quiz_schedule,
    claim_due_schedules,
    create_quiz_schedule,
    get_or_create_user,
    get_quiz_schedule_by_id,
    get_user_quiz_schedules,
    recover_stale_leases,
    save_quiz_batch,
    update_schedule_heartbeat,
)
from app.parser.models import QuizQuestion, QuizSettings
from app.services.publishing_service import PublishingService, PublishingSummary
from app.services.scheduler_service import QuizSchedulerService
from app.services.telegram_service import TelegramService


@pytest.fixture(autouse=True)
def init_db_tables():
    """Ensure database tables exist for all handlers and services using SessionLocal.

    Safety guard: Guarantees tests fail closed and never drop non-SQLite databases.
    """
    assert engine.url.drivername == "sqlite", (
        "CRITICAL SAFETY VIOLATION: Test suite attempted to run against non-SQLite database! "
        f"Driver: {engine.url.drivername}"
    )
    Base.metadata.create_all(bind=engine)
    yield
    with engine.begin() as conn:
        Base.metadata.drop_all(bind=conn)


@pytest.fixture
def db_session():
    """Session tied to test engine."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_user_and_quiz(db_session):
    """Create a sample user and quiz set for scheduling tests."""
    user = get_or_create_user(db_session, telegram_id=555123, username="sched_tester")
    questions = [
        QuizQuestion(
            question="What is the capital of India?",
            options=["Mumbai", "New Delhi", "Kolkata", "Chennai"],
            correct_option=1,
            explanation="New Delhi is the capital.",
        ),
        QuizQuestion(
            question="Which planet is known as the Red Planet?",
            options=["Venus", "Mars", "Jupiter", "Saturn"],
            correct_option=1,
        ),
    ]
    settings = QuizSettings(
        title="General Knowledge Quiz",
        description="Sample test quiz",
        is_anonymous=True,
        time_limit=30,
    )
    quiz_set = save_quiz_batch(
        db=db_session,
        user_id=user.id,
        questions=questions,
        settings=settings,
        title="General Knowledge Quiz",
        description="Sample test quiz",
    )
    db_session.refresh(user)
    db_session.refresh(quiz_set)
    db_session.expunge(user)
    db_session.expunge(quiz_set)
    return user, quiz_set


# ============================================================================
# 1. Database Model & Repository Tests
# ============================================================================


def test_schedule_creation_and_retrieval(db_session, sample_user_and_quiz):
    """Verify creating and retrieving a quiz schedule with timezone conversion."""
    user, quiz_set = sample_user_and_quiz
    future_time_utc = datetime.now(UTC) + timedelta(hours=2)

    schedule = create_quiz_schedule(
        db=db_session,
        user_id=user.id,
        quiz_set_id=quiz_set.id,
        chat_id="-100987654321",
        scheduled_at=future_time_utc,
        timezone_str="Asia/Kolkata",
        chat_title="My Test Group",
    )

    assert schedule.id is not None
    assert schedule.status == "pending"
    assert schedule.chat_id == "-100987654321"
    assert schedule.chat_title == "My Test Group"
    assert schedule.timezone == "Asia/Kolkata"

    # Retrieve by ID
    fetched = get_quiz_schedule_by_id(db_session, schedule.id, user_id=user.id)
    assert fetched is not None
    assert fetched.id == schedule.id
    assert fetched.quiz_set.title == "General Knowledge Quiz"


def test_schedule_creation_rejects_past_time(db_session, sample_user_and_quiz):
    """Verify that scheduling a quiz in the past raises ValueError."""
    user, quiz_set = sample_user_and_quiz
    past_time_utc = datetime.now(UTC) - timedelta(minutes=10)

    with pytest.raises(ValueError, match="must be in the future"):
        create_quiz_schedule(
            db=db_session,
            user_id=user.id,
            quiz_set_id=quiz_set.id,
            chat_id="-100987654321",
            scheduled_at=past_time_utc,
        )


def test_schedule_creation_ownership_enforcement(db_session, sample_user_and_quiz):
    """Verify that a user cannot schedule a quiz they do not own."""
    _, quiz_set = sample_user_and_quiz
    other_user = get_or_create_user(db_session, telegram_id=999888, username="intruder")
    future_time = datetime.now(UTC) + timedelta(hours=1)

    with pytest.raises(ValueError, match="not found or not owned"):
        create_quiz_schedule(
            db=db_session,
            user_id=other_user.id,
            quiz_set_id=quiz_set.id,
            chat_id="-100987654321",
            scheduled_at=future_time,
        )


def test_schedule_cancellation(db_session, sample_user_and_quiz):
    """Verify cancelling a pending schedule."""
    user, quiz_set = sample_user_and_quiz
    future_time = datetime.now(UTC) + timedelta(hours=3)

    sched = create_quiz_schedule(
        db=db_session,
        user_id=user.id,
        quiz_set_id=quiz_set.id,
        chat_id="-100987654321",
        scheduled_at=future_time,
    )

    # Unauthorized cancel attempt
    success, msg, _ = cancel_quiz_schedule(db_session, sched.id, user_id=99999)
    assert not success
    assert "unauthorized" in msg.lower()

    # Authorized cancel
    success, msg, cancelled = cancel_quiz_schedule(db_session, sched.id, user_id=user.id)
    assert success
    assert cancelled.status == "cancelled"

    # Cannot cancel already cancelled schedule
    success2, msg2, _ = cancel_quiz_schedule(db_session, sched.id, user_id=user.id)
    assert not success2
    assert "cancelled" in msg2


def test_user_schedule_pagination_and_filtering(db_session, sample_user_and_quiz):
    """Verify listing user schedules with status filtering and pagination."""
    user, quiz_set = sample_user_and_quiz

    for i in range(1, 8):
        future_time = datetime.now(UTC) + timedelta(hours=i)
        create_quiz_schedule(
            db=db_session,
            user_id=user.id,
            quiz_set_id=quiz_set.id,
            chat_id=f"-100{i}",
            scheduled_at=future_time,
            chat_title=f"Group {i}",
        )

    # Page 0 (5 items)
    items, total = get_user_quiz_schedules(db_session, user_id=user.id, limit=5, offset=0)
    assert total == 7
    assert len(items) == 5

    # Page 1 (2 items)
    items_p1, _ = get_user_quiz_schedules(db_session, user_id=user.id, limit=5, offset=5)
    assert len(items_p1) == 2


# ============================================================================
# 2. Scheduler Worker & Concurrency Tests
# ============================================================================


def test_claim_due_schedules_atomic(db_session, sample_user_and_quiz):
    """Verify due tasks are claimed and future tasks are ignored."""
    user, quiz_set = sample_user_and_quiz
    now_utc = datetime.now(UTC).replace(tzinfo=None)

    # Task 1: Due (scheduled in past relative to simulated claim time)
    s1 = QuizSchedule(
        user_id=user.id,
        quiz_set_id=quiz_set.id,
        chat_id="-1001",
        scheduled_at=now_utc - timedelta(minutes=5),
        status="pending",
    )
    # Task 2: Not due (scheduled in future)
    s2 = QuizSchedule(
        user_id=user.id,
        quiz_set_id=quiz_set.id,
        chat_id="-1002",
        scheduled_at=now_utc + timedelta(hours=1),
        status="pending",
    )
    db_session.add_all([s1, s2])
    db_session.commit()

    # Worker 1 claims due tasks
    claimed = claim_due_schedules(
        db_session, now_utc=now_utc, batch_size=10, lease_token="worker-token-A"
    )
    assert len(claimed) == 1
    assert claimed[0].id == s1.id

    # Verify task 1 is now 'running' and has lease token
    refreshed_s1 = get_quiz_schedule_by_id(db_session, s1.id)
    assert refreshed_s1.status == "running"
    assert refreshed_s1.lease_token == "worker-token-A"

    # Worker 2 attempts claim at same time - should claim 0 (s1 already claimed, s2 not due)
    claimed_worker_2 = claim_due_schedules(
        db_session, now_utc=now_utc, batch_size=10, lease_token="worker-token-B"
    )
    assert len(claimed_worker_2) == 0


def test_heartbeat_and_lease_recovery(db_session, sample_user_and_quiz):
    """Verify lease heartbeats and recovering crashed worker leases."""
    user, quiz_set = sample_user_and_quiz
    now_utc = datetime.now(UTC).replace(tzinfo=None)

    schedule = QuizSchedule(
        user_id=user.id,
        quiz_set_id=quiz_set.id,
        chat_id="-1001",
        scheduled_at=now_utc - timedelta(minutes=10),
        status="running",
        lease_token="crashed-worker-token",
        last_heartbeat_at=now_utc - timedelta(minutes=15),  # 15 mins ago
    )
    db_session.add(schedule)
    db_session.commit()

    # Heartbeat check with wrong lease token fails
    assert not update_schedule_heartbeat(db_session, schedule.id, "wrong-token")

    # Heartbeat check with correct token succeeds
    assert update_schedule_heartbeat(db_session, schedule.id, "crashed-worker-token")

    # Set back to stale to test recovery
    schedule.last_heartbeat_at = now_utc - timedelta(minutes=15)
    db_session.commit()

    cutoff = now_utc - timedelta(minutes=5)
    recovered = recover_stale_leases(db_session, stale_before_utc=cutoff)
    assert len(recovered) == 1
    assert recovered[0].id == schedule.id
    assert recovered[0].status == "failed"
    assert "lease expired" in recovered[0].error_message.lower()


# ============================================================================
# 3. Scheduled Publishing Execution Tests
# ============================================================================


@pytest.mark.asyncio
async def test_scheduler_service_successful_execution(sample_user_and_quiz):
    """Verify end-to-end execution of a due schedule by the QuizSchedulerService."""
    user, quiz_set = sample_user_and_quiz

    mock_bot = MagicMock(spec=Bot)
    mock_bot.send_message = AsyncMock()

    mock_telegram = MagicMock(spec=TelegramService)
    mock_telegram.validate_channel_access = AsyncMock(return_value=(True, "Verified", -100999))

    mock_publisher = MagicMock(spec=PublishingService)
    mock_publisher.publish_batch = AsyncMock(
        return_value=PublishingSummary(total=2, successful=2, failed=0)
    )

    scheduler = QuizSchedulerService(
        bot=mock_bot,
        publishing_service=mock_publisher,
        telegram_service=mock_telegram,
    )

    # Patch SessionLocal to use test_db
    with patch("app.services.scheduler_service.SessionLocal") as mock_session_factory:
        from app.database.database import SessionLocal

        mock_session_factory.side_effect = SessionLocal

        # Create schedule
        future_time = datetime.now(UTC) + timedelta(minutes=1)
        with SessionLocal() as db:
            sched = create_quiz_schedule(
                db=db,
                user_id=user.id,
                quiz_set_id=quiz_set.id,
                chat_id="-100999",
                scheduled_at=future_time,
                chat_title="Exam Group",
            )
            sched_id = sched.id

        # Execute
        success = await scheduler.execute_schedule(sched_id, "test-lease")
        assert success

        # Verify DB status updated to 'completed'
        with SessionLocal() as db:
            done_sched = get_quiz_schedule_by_id(db, sched_id)
            assert done_sched.status == "completed"
            assert done_sched.executed_at is not None

        # Verify existing publishing service was invoked with correct arguments
        assert mock_publisher.publish_batch.called
        call_kwargs = mock_publisher.publish_batch.call_args[1]
        assert call_kwargs["target_chat_id"] == "-100999"
        assert len(call_kwargs["questions"]) == 2

        # Verify user was sent execution receipt notification
        assert mock_bot.send_message.called
        notify_text = mock_bot.send_message.call_args[1]["text"]
        assert "Scheduled Quiz Conducted" in notify_text
        assert "General Knowledge Quiz" in notify_text


@pytest.mark.asyncio
async def test_scheduler_service_permission_failure(sample_user_and_quiz):
    """Verify schedule is marked failed when bot lacks group permissions."""
    user, quiz_set = sample_user_and_quiz

    mock_bot = MagicMock(spec=Bot)
    mock_bot.send_message = AsyncMock()

    mock_telegram = MagicMock(spec=TelegramService)
    mock_telegram.validate_channel_access = AsyncMock(
        return_value=(False, "Bot is not an Admin in channel", None)
    )

    mock_publisher = MagicMock(spec=PublishingService)

    scheduler = QuizSchedulerService(
        bot=mock_bot,
        publishing_service=mock_publisher,
        telegram_service=mock_telegram,
    )

    from app.database.database import SessionLocal

    with SessionLocal() as db:
        sched = create_quiz_schedule(
            db=db,
            user_id=user.id,
            quiz_set_id=quiz_set.id,
            chat_id="-100555",
            scheduled_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        sched_id = sched.id

    success = await scheduler.execute_schedule(sched_id, "test-lease")
    assert not success

    with SessionLocal() as db:
        failed_sched = get_quiz_schedule_by_id(db, sched_id)
        assert failed_sched.status == "failed"
        assert "verification failed" in failed_sched.error_message.lower()

    # Publisher should not have been called
    assert not mock_publisher.publish_batch.called


# ============================================================================
# 4. Telegram UI & Keyboards Tests
# ============================================================================


def test_automation_menu_keyboard_structure():
    """Verify automation hub keyboard options."""
    kb = get_automation_menu_keyboard()
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "action_schedule_start" in flat
    assert "action_my_schedules_0" in flat
    assert "action_cancel_schedule_menu" in flat
    assert "action_main_menu" in flat


def test_date_keyboard_generation_future_days():
    """Verify date picker buttons contain today, tomorrow, and future days."""
    kb = get_schedule_date_keyboard(user_tz="Asia/Kolkata")
    texts = [b.text for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

    assert any("Today" in t for t in texts)
    assert any("Tomorrow" in t for t in texts)
    assert any(c.startswith("sched_date_") for c in callbacks)


def test_time_keyboard_generation_and_tz_display():
    """Verify time slot buttons and timezone button."""
    kb = get_schedule_time_keyboard(selected_date_str="2026-11-25", user_tz="Asia/Kolkata")
    texts = [b.text for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

    assert any("09:00 AM" in t for t in texts)
    assert any("07:00 PM" in t for t in texts)
    assert any("Asia/Kolkata" in t for t in texts)
    assert "sched_select_tz" in callbacks


def test_timezone_keyboard_options():
    """Verify common timezones are selectable."""
    kb = get_schedule_timezone_keyboard()
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

    assert "sched_set_tz_Asia/Kolkata" in callbacks
    assert "sched_set_tz_UTC" in callbacks
    assert "sched_set_tz_America/New_York" in callbacks


# ============================================================================
# 5. Full Interactive Scheduling Conversation Flow Test
# ============================================================================


@pytest.mark.asyncio
async def test_full_scheduling_conversation_flow(sample_user_and_quiz):
    """Simulate complete user conversation: select quiz -> select group -> select date -> select time -> confirm."""
    user, quiz_set = sample_user_and_quiz

    # Mock telegram bot and context
    mock_bot = MagicMock(spec=Bot)
    mock_bot.send_message = AsyncMock()
    mock_bot.get_chat = AsyncMock(return_value=MagicMock(title="Public Study Group", id=-100888999))

    context = MagicMock()
    context.bot = mock_bot
    context.user_data = {}

    tg_user = MagicMock(spec=TgUser)
    tg_user.id = user.telegram_id
    tg_user.username = user.username

    # Step 1: User taps "Schedule a Quiz"
    up_step1 = MagicMock(spec=Update)
    up_step1.effective_user = tg_user
    q1 = MagicMock(spec=CallbackQuery)
    q1.answer = AsyncMock()
    q1.message = MagicMock(spec=Message)
    q1.message.edit_message_text = AsyncMock()
    up_step1.callback_query = q1
    up_step1.message = None

    state1 = await start_schedule_flow(up_step1, context)
    assert state1 == QuizScheduleState.SELECTING_QUIZ

    # Step 2: User picks quiz
    up_step2 = MagicMock(spec=Update)
    up_step2.effective_user = tg_user
    q2 = MagicMock(spec=CallbackQuery)
    q2.data = f"sched_pick_quiz_{quiz_set.id}"
    q2.answer = AsyncMock()
    q2.message = MagicMock(spec=Message)
    q2.message.edit_message_text = AsyncMock()
    up_step2.callback_query = q2

    state2 = await pick_quiz_callback(up_step2, context)
    assert state2 == QuizScheduleState.SELECTING_GROUP
    assert context.user_data["schedule_quiz_id"] == quiz_set.id

    # Step 3: User sends destination group @username
    with patch(
        "app.bot.handlers.schedule.telegram_service.validate_channel_access",
        AsyncMock(return_value=(True, "Verified", -100888999)),
    ):
        up_step3 = MagicMock(spec=Update)
        up_step3.effective_user = tg_user
        msg3 = MagicMock(spec=Message)
        msg3.text = "@PublicStudyGroup"
        msg3.reply_text = AsyncMock()
        up_step3.message = msg3
        up_step3.callback_query = None

        state3 = await receive_group_text_message(up_step3, context)
        assert state3 == QuizScheduleState.SELECTING_DATE
        assert context.user_data["schedule_chat_id"] == "-100888999"

    # Step 4: User selects tomorrow's date
    tomorrow = (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=1)).strftime("%Y-%m-%d")
    up_step4 = MagicMock(spec=Update)
    up_step4.effective_user = tg_user
    q4 = MagicMock(spec=CallbackQuery)
    q4.data = f"sched_date_{tomorrow}"
    q4.answer = AsyncMock()
    q4.message = MagicMock(spec=Message)
    q4.message.edit_message_text = AsyncMock()
    up_step4.callback_query = q4

    state4 = await pick_date_callback(up_step4, context)
    assert state4 == QuizScheduleState.SELECTING_TIME
    assert context.user_data["schedule_date"] == tomorrow

    # Step 5: User selects 07:00 PM (19:00)
    up_step5 = MagicMock(spec=Update)
    up_step5.effective_user = tg_user
    q5 = MagicMock(spec=CallbackQuery)
    q5.data = "sched_time_19:00"
    q5.answer = AsyncMock()
    q5.message = MagicMock(spec=Message)
    q5.message.edit_message_text = AsyncMock()
    up_step5.callback_query = q5

    state5 = await pick_time_callback(up_step5, context)
    assert state5 == QuizScheduleState.CONFIRMING_SCHEDULE
    assert context.user_data["schedule_time"] == "19:00"
    assert "schedule_scheduled_at_utc" in context.user_data

    # Step 6: User clicks "Confirm Schedule"
    up_step6 = MagicMock(spec=Update)
    up_step6.effective_user = tg_user
    q6 = MagicMock(spec=CallbackQuery)
    q6.data = "sched_confirm_schedule"
    q6.answer = AsyncMock()
    q6.edit_message_text = AsyncMock()
    q6.message = MagicMock(spec=Message)
    up_step6.callback_query = q6

    final_state = await confirm_schedule_callback(up_step6, context)
    assert final_state == ConversationHandler.END

    # Verify confirmation text was rendered
    assert q6.edit_message_text.called
    conf_call_text = q6.edit_message_text.call_args[0][0]
    assert "Quiz Scheduled Successfully" in conf_call_text
    assert "General Knowledge Quiz" in conf_call_text
