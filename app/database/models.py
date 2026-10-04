"""SQLAlchemy ORM models for QuizBotPro."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """User account mapped to Telegram user."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    quiz_sets = relationship("QuizSet", back_populates="user", cascade="all, delete-orphan")


class QuizSet(Base):
    """A batch/set of questions created by a user."""

    __tablename__ = "quiz_sets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), default="Untitled Quiz Set")
    description = Column(Text, nullable=True)
    status = Column(String(50), default="draft")  # draft, ready, published
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="quiz_sets")
    questions = relationship("Question", back_populates="quiz_set", cascade="all, delete-orphan", order_by="Question.position", lazy="selectin")
    settings = relationship("QuizSettingsRecord", uselist=False, back_populates="quiz_set", cascade="all, delete-orphan", lazy="selectin")
    publish_jobs = relationship("PublishJob", back_populates="quiz_set", cascade="all, delete-orphan", lazy="selectin")
    battles = relationship("BattleRecord", back_populates="quiz_set", cascade="all, delete-orphan", lazy="selectin")


class Question(Base):
    """A single question belonging to a QuizSet."""

    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    quiz_set_id = Column(Integer, ForeignKey("quiz_sets.id"), nullable=False)
    question = Column(Text, nullable=False)
    correct_option = Column(Integer, nullable=False)
    explanation = Column(Text, nullable=True)
    position = Column(Integer, default=0)

    quiz_set = relationship("QuizSet", back_populates="questions")
    options = relationship("Option", back_populates="question", cascade="all, delete-orphan", order_by="Option.position", lazy="selectin")


class Option(Base):
    """An option belonging to a Question."""

    __tablename__ = "options"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    option_text = Column(String(255), nullable=False)
    position = Column(Integer, default=0)

    question = relationship("Question", back_populates="options")


class QuizSettingsRecord(Base):
    """Persisted settings for a quiz set."""

    __tablename__ = "quiz_settings"

    id = Column(Integer, primary_key=True, index=True)
    quiz_set_id = Column(Integer, ForeignKey("quiz_sets.id"), nullable=False, unique=True)
    is_anonymous = Column(Boolean, default=True)
    shuffle_options = Column(Boolean, default=False)
    explanation_enabled = Column(Boolean, default=True)
    time_limit = Column(Integer, nullable=True)
    channel_id = Column(String(255), nullable=True)

    quiz_set = relationship("QuizSet", back_populates="settings")


class PublishJob(Base):
    """Tracks a bulk publishing job execution."""

    __tablename__ = "publish_jobs"

    id = Column(Integer, primary_key=True, index=True)
    quiz_set_id = Column(Integer, ForeignKey("quiz_sets.id"), nullable=False)
    status = Column(String(50), default="pending")  # pending, in_progress, completed, failed
    total = Column(Integer, default=0)
    successful = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)

    quiz_set = relationship("QuizSet", back_populates="publish_jobs")


class BattleRecord(Base):
    """Tracks a completed live group quiz battle and winner."""

    __tablename__ = "battle_records"

    id = Column(Integer, primary_key=True, index=True)
    quiz_set_id = Column(Integer, ForeignKey("quiz_sets.id"), nullable=False)
    chat_id = Column(String(255), nullable=False)
    chat_title = Column(String(255), nullable=True)
    winner_name = Column(String(255), nullable=True)
    winner_score = Column(Integer, default=0)
    total_participants = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)

    quiz_set = relationship("QuizSet", back_populates="battles")

