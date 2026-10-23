"""Repository functions for database interactions."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session, selectinload

from app.database.models import (
    BattleRecord,
    Option,
    PublishJob,
    Question,
    QuizSet,
    QuizSettingsRecord,
    User,
)
from app.parser.models import QuizQuestion, QuizSettings


def get_or_create_user(db: Session, telegram_id: int, username: Optional[str] = None) -> User:
    """Find existing user or create a new user record."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, username=username)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif username and user.username != username:
        user.username = username
        db.commit()
        db.refresh(user)
    return user


def save_quiz_batch(
    db: Session,
    user_id: int,
    questions: list[QuizQuestion],
    settings: QuizSettings,
    title: str = "Bulk Quiz Batch",
    description: Optional[str] = None,
) -> QuizSet:
    """Persist a parsed quiz batch with settings into the database."""
    quiz_set = QuizSet(user_id=user_id, title=title, description=description, status="ready")
    db.add(quiz_set)
    db.flush()

    # Save Settings
    settings_rec = QuizSettingsRecord(
        quiz_set_id=quiz_set.id,
        is_anonymous=settings.is_anonymous,
        shuffle_options=settings.shuffle_options,
        explanation_enabled=settings.explanation_enabled,
        time_limit=settings.time_limit,
        channel_id=settings.channel_id,
    )
    db.add(settings_rec)

    # Save Questions and Options
    for pos, q in enumerate(questions):
        q_rec = Question(
            quiz_set_id=quiz_set.id,
            question=q.question,
            correct_option=q.correct_option,
            explanation=q.explanation,
            position=pos,
        )
        db.add(q_rec)
        db.flush()

        for opt_pos, opt_text in enumerate(q.options):
            opt_rec = Option(
                question_id=q_rec.id,
                option_text=opt_text,
                position=opt_pos,
            )
            db.add(opt_rec)

    db.commit()
    db.refresh(quiz_set)
    return quiz_set


def create_publish_job(db: Session, quiz_set_id: int, total: int) -> PublishJob:
    """Create a tracking record for a publishing execution."""
    job = PublishJob(
        quiz_set_id=quiz_set_id,
        status="in_progress",
        total=total,
        successful=0,
        failed=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def complete_publish_job(
    db: Session, job_id: int, successful: int, failed: int, status: str = "completed"
) -> PublishJob:
    """Update job results upon completion."""
    job = db.query(PublishJob).filter(PublishJob.id == job_id).first()
    if job:
        job.successful = successful
        job.failed = failed
        job.status = status
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
    return job


def get_user_quiz_sets(
    db: Session, telegram_id: int, limit: int = 5, offset: int = 0
) -> tuple[list[QuizSet], int]:
    """Retrieve paginated quiz sets for a Telegram user with total count."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        return [], 0
    query = (
        db.query(QuizSet)
        .options(
            selectinload(QuizSet.questions).selectinload(Question.options),
            selectinload(QuizSet.settings),
            selectinload(QuizSet.publish_jobs),
        )
        .filter(QuizSet.user_id == user.id)
        .order_by(QuizSet.created_at.asc(), QuizSet.id.asc())
    )
    total_count = db.query(QuizSet).filter(QuizSet.user_id == user.id).count()
    quiz_sets = query.offset(offset).limit(limit).all()
    return quiz_sets, total_count


def get_quiz_set_by_id(
    db: Session, quiz_set_id: int, telegram_id: Optional[int] = None
) -> Optional[QuizSet]:
    """Retrieve a specific quiz set by ID, optionally verifying ownership."""
    query = (
        db.query(QuizSet)
        .options(
            selectinload(QuizSet.questions).selectinload(Question.options),
            selectinload(QuizSet.settings),
            selectinload(QuizSet.publish_jobs),
        )
        .filter(QuizSet.id == quiz_set_id)
    )
    if telegram_id is not None:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return None
        query = query.filter(QuizSet.user_id == user.id)
    return query.first()


def delete_quiz_set(db: Session, quiz_set_id: int, telegram_id: int) -> bool:
    """Delete a quiz set owned by a user."""
    quiz_set = get_quiz_set_by_id(db, quiz_set_id, telegram_id)
    if not quiz_set:
        return False
    db.delete(quiz_set)
    db.commit()
    return True


def reconstruct_quiz_data(quiz_set: QuizSet) -> tuple[list[QuizQuestion], QuizSettings]:
    """Reconstruct domain QuizQuestion list and QuizSettings from database records."""
    questions: list[QuizQuestion] = []
    for q in quiz_set.questions:
        options_sorted = sorted(q.options, key=lambda opt: opt.position)
        options_text = [opt.option_text for opt in options_sorted]
        questions.append(
            QuizQuestion(
                question=q.question,
                options=options_text,
                correct_option=q.correct_option,
                explanation=q.explanation,
                question_number=q.position + 1,
            )
        )

    rec = quiz_set.settings
    settings = QuizSettings(
        title=quiz_set.title,
        description=quiz_set.description,
        is_anonymous=rec.is_anonymous if rec else False,
        shuffle_options=rec.shuffle_options if rec else False,
        explanation_enabled=rec.explanation_enabled if rec else True,
        time_limit=rec.time_limit if (rec and rec.time_limit is not None) else 15,
        channel_id=rec.channel_id if rec else None,
        header_banner_enabled=True,
    )
    return questions, settings


def record_battle_result(
    db: Session,
    quiz_set_id: int,
    chat_id: str | int,
    chat_title: Optional[str],
    winner_name: Optional[str],
    winner_score: int,
    total_participants: int,
) -> BattleRecord:
    """Record the outcome of a live group quiz battle."""
    record = BattleRecord(
        quiz_set_id=quiz_set_id,
        chat_id=str(chat_id),
        chat_title=chat_title,
        winner_name=winner_name,
        winner_score=winner_score,
        total_participants=total_participants,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_quiz_set_questions(
    db: Session, quiz_set_id: int, questions: list[QuizQuestion]
) -> None:
    """Synchronize updated questions for an existing quiz set in the database."""
    quiz_set = (
        db.query(QuizSet)
        .options(selectinload(QuizSet.questions).selectinload(Question.options))
        .filter(QuizSet.id == quiz_set_id)
        .first()
    )
    if not quiz_set:
        return

    # Delete existing questions (cascade deletes options)
    for q in list(quiz_set.questions):
        db.delete(q)
    db.flush()

    for pos, q in enumerate(questions):
        q_rec = Question(
            quiz_set_id=quiz_set.id,
            question=q.question,
            correct_option=q.correct_option,
            explanation=q.explanation,
            position=pos,
        )
        db.add(q_rec)
        db.flush()

        for opt_pos, opt_text in enumerate(q.options):
            opt_rec = Option(
                question_id=q_rec.id,
                option_text=opt_text,
                position=opt_pos,
            )
            db.add(opt_rec)

    db.commit()


def update_quiz_set_timer(db: Session, quiz_set_id: int, time_limit: Optional[int]) -> None:
    """Update time limit setting for a quiz set."""
    rec = db.query(QuizSettingsRecord).filter(QuizSettingsRecord.quiz_set_id == quiz_set_id).first()
    if rec:
        rec.time_limit = time_limit
        db.commit()


def update_quiz_set_settings(db: Session, quiz_set_id: int, settings: QuizSettings) -> None:
    """Update all persisted settings for a quiz set."""
    quiz_set = db.query(QuizSet).filter(QuizSet.id == quiz_set_id).first()
    if not quiz_set:
        return
    if settings.title:
        quiz_set.title = settings.title
    if settings.description is not None:
        quiz_set.description = settings.description

    rec = db.query(QuizSettingsRecord).filter(QuizSettingsRecord.quiz_set_id == quiz_set_id).first()
    if rec:
        rec.is_anonymous = settings.is_anonymous
        rec.shuffle_options = settings.shuffle_options
        rec.explanation_enabled = settings.explanation_enabled
        rec.time_limit = settings.time_limit
        rec.channel_id = settings.channel_id
    else:
        rec = QuizSettingsRecord(
            quiz_set_id=quiz_set_id,
            is_anonymous=settings.is_anonymous,
            shuffle_options=settings.shuffle_options,
            explanation_enabled=settings.explanation_enabled,
            time_limit=settings.time_limit,
            channel_id=settings.channel_id,
        )
        db.add(rec)
    db.commit()




