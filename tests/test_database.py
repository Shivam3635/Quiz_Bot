"""Unit tests for SQLite database operations and repositories."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.database.models import User, QuizSet, Question, Option, PublishJob
from app.database.repositories import (
    get_or_create_user,
    save_quiz_batch,
    create_publish_job,
    complete_publish_job,
)
from app.parser.models import QuizQuestion, QuizSettings


@pytest.fixture
def test_db():
    """In-memory SQLite database session fixture."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_user_repository(test_db):
    """Test creating and retrieving users."""
    u1 = get_or_create_user(test_db, telegram_id=999, username="quiz_tester")
    assert u1.id is not None
    assert u1.telegram_id == 999
    assert u1.username == "quiz_tester"

    # Fetch again - should retrieve same user
    u2 = get_or_create_user(test_db, telegram_id=999, username="new_name")
    assert u2.id == u1.id
    assert u2.username == "new_name"


def test_save_quiz_batch_and_job(test_db):
    """Test persisting full quiz batch and publish job tracking."""
    user = get_or_create_user(test_db, telegram_id=123, username="creator")

    questions = [
        QuizQuestion(
            question="What is 10 x 10?",
            options=["10", "100", "1000"],
            correct_option=1,
            explanation="10 * 10 is 100",
        ),
        QuizQuestion(
            question="What is the boiling point of water?",
            options=["50°C", "100°C"],
            correct_option=1,
        ),
    ]
    settings = QuizSettings(is_anonymous=True, time_limit=30, channel_id="@testchannel")

    quiz_set = save_quiz_batch(
        test_db,
        user_id=user.id,
        questions=questions,
        settings=settings,
        title="Math & Science Test",
    )

    assert quiz_set.id is not None
    assert len(quiz_set.questions) == 2
    assert quiz_set.questions[0].options[1].option_text == "100"
    assert quiz_set.settings.time_limit == 30
    assert quiz_set.settings.channel_id == "@testchannel"

    # Test publish job
    job = create_publish_job(test_db, quiz_set_id=quiz_set.id, total=2)
    assert job.status == "in_progress"
    assert job.total == 2

    completed_job = complete_publish_job(test_db, job_id=job.id, successful=2, failed=0)
    assert completed_job.status == "completed"
    assert completed_job.successful == 2
    assert completed_job.failed == 0
    assert completed_job.completed_at is not None


def test_myquizzes_repository_operations(test_db):
    """Test retrieving, reconstructing, and deleting quiz sets for /myquizzes."""
    from app.database.repositories import (
        delete_quiz_set,
        get_quiz_set_by_id,
        get_user_quiz_sets,
        reconstruct_quiz_data,
    )

    user = get_or_create_user(test_db, telegram_id=777, username="archivist")

    questions = [
        QuizQuestion(
            question="What is the capital of France?",
            options=["Berlin", "Madrid", "Paris"],
            correct_option=2,
            explanation="Paris is France's capital.",
        ),
    ]
    settings = QuizSettings(title="Geography Quickfire", description="Europe facts", time_limit=15)

    qs = save_quiz_batch(
        test_db,
        user_id=user.id,
        questions=questions,
        settings=settings,
        title=settings.title,
        description=settings.description,
    )

    # 1. Test get_user_quiz_sets
    sets, count = get_user_quiz_sets(test_db, telegram_id=777, limit=5, offset=0)
    assert count == 1
    assert len(sets) == 1
    assert sets[0].title == "Geography Quickfire"
    assert sets[0].description == "Europe facts"

    # 2. Test get_quiz_set_by_id
    fetched = get_quiz_set_by_id(test_db, quiz_set_id=qs.id, telegram_id=777)
    assert fetched is not None
    assert fetched.id == qs.id

    # Non-owner should not access
    assert get_quiz_set_by_id(test_db, quiz_set_id=qs.id, telegram_id=888) is None

    # 3. Test reconstruct_quiz_data
    recon_qs, recon_settings = reconstruct_quiz_data(fetched)
    assert len(recon_qs) == 1
    assert recon_qs[0].question == "What is the capital of France?"
    assert recon_qs[0].options == ["Berlin", "Madrid", "Paris"]
    assert recon_qs[0].correct_option == 2
    assert recon_settings.title == "Geography Quickfire"
    assert recon_settings.description == "Europe facts"
    assert recon_settings.time_limit == 15

    # 4. Test delete_quiz_set
    deleted = delete_quiz_set(test_db, quiz_set_id=qs.id, telegram_id=777)
    assert deleted is True

    # Confirm deletion
    sets_after, count_after = get_user_quiz_sets(test_db, telegram_id=777)
    assert count_after == 0
    assert len(sets_after) == 0

