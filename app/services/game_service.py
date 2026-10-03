"""Live Group Quiz Game Service & Session Manager."""

import asyncio
import html
import time
from dataclasses import dataclass, field
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode, PollType

from app.database.database import SessionLocal
from app.database.repositories import record_battle_result
from app.parser.models import QuizQuestion
from app.parser.validator import validate_option_length
from app.utils.helpers import format_bilingual_question_text, format_rich_text_for_telegram
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class ParticipantScore:
    """Tracks a single user's live game performance."""

    user_id: int
    full_name: str
    username: Optional[str] = None
    score: int = 0
    total_time: float = 0.0
    answers: dict[int, bool] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """Formatted display name with mention if available."""
        if self.username:
            return f"@{self.username}"
        return html.escape(self.full_name)


@dataclass
class GameSession:
    """Represents a live group quiz battle session."""

    chat_id: int
    chat_title: Optional[str]
    quiz_set_id: int
    title: str
    description: Optional[str]
    questions: list[QuizQuestion]
    time_limit: int = 15  # seconds per question
    status: str = "lobby"  # lobby, countdown, running, finished, stopped
    initiator_id: int = 0
    initiator_name: str = ""
    lobby_message_id: Optional[int] = None
    current_question_idx: int = 0
    current_poll_id: Optional[str] = None
    current_poll_message_id: Optional[int] = None
    question_start_time: float = 0.0
    scores: dict[int, ParticipantScore] = field(default_factory=dict)
    round_correct: dict[int, float] = field(default_factory=dict)  # user_id -> time_taken
    round_all_answers: set[int] = field(default_factory=set)  # user_ids who answered current question
    task: Optional[asyncio.Task] = None

    def add_participant(self, user_id: int, full_name: str, username: Optional[str] = None) -> bool:
        """Add user to lobby participants. Returns True if newly added."""
        if user_id not in self.scores:
            self.scores[user_id] = ParticipantScore(
                user_id=user_id,
                full_name=full_name,
                username=username,
            )
            return True
        return False

    def record_answer(
        self,
        user_id: int,
        full_name: str,
        username: Optional[str],
        selected_option: int,
        correct_option: int,
    ) -> bool:
        """Process incoming poll answer for the active question."""
        if self.status != "running":
            return False

        # Ensure participant is registered
        if user_id not in self.scores:
            self.add_participant(user_id=user_id, full_name=full_name, username=username)

        participant = self.scores[user_id]
        time_taken = max(0.1, time.time() - self.question_start_time)
        self.round_all_answers.add(user_id)

        is_correct = selected_option == correct_option
        participant.answers[self.current_question_idx] = is_correct

        if is_correct:
            participant.score += 1
            participant.total_time += time_taken
            self.round_correct[user_id] = time_taken
            logger.info(
                "User %s answered Q%d correctly in %.2fs",
                participant.display_name,
                self.current_question_idx + 1,
                time_taken,
            )
            return True

        logger.info(
            "User %s answered Q%d incorrectly in %.2fs",
            participant.display_name,
            self.current_question_idx + 1,
            time_taken,
        )
        return False

    def get_sorted_leaderboard(self) -> list[ParticipantScore]:
        """Rank participants by score (descending), then response speed (ascending)."""
        return sorted(
            self.scores.values(),
            key=lambda p: (-p.score, p.total_time),
        )

    def format_lobby_text(self) -> str:
        """Render the group battle lobby announcement."""
        total_q = len(self.questions)
        desc_line = f"📝 <i>{html.escape(self.description)}</i>\n\n" if self.description else "\n"
        player_count = len(self.scores)

        players_list = ""
        if self.scores:
            players_list = "\n".join(
                f"• {p.display_name}" for p in self.scores.values()
            )
        else:
            players_list = "<i>No players joined yet. Tap 'Join Quiz' below!</i>"

        wait_note = (
            f"⏳ <i>Waiting for at least 2 players to vote I am ready ({player_count}/2)...</i>"
            if player_count < 2
            else f"✅ <i>{player_count} players ready! Starting...</i>"
        )

        return (
            f"🎮 <b>Live Quiz Battle: {html.escape(self.title)}</b>\n"
            f"{desc_line}"
            f"📊 <b>Questions:</b> <code>{total_q}</code>\n"
            f"⏱️ <b>Time per question:</b> <code>{self.time_limit}s</code>\n"
            f"👑 <b>Host:</b> {html.escape(self.initiator_name)}\n\n"
            f"👥 <b>Ready Players ({player_count}):</b>\n"
            f"{players_list}\n\n"
            f"{wait_note}\n\n"
            f"<i>Tap <b>🙋 I am ready!</b> to vote. The quiz starts automatically when 2 players are ready!</i>"
        )

    def format_leaderboard_text(self) -> str:
        """Render final podium leaderboard rankings."""
        total_q = len(self.questions)
        ranked = self.get_sorted_leaderboard()

        medals = ["🥇", "🥈", "🥉"]
        lines: list[str] = []

        for idx, p in enumerate(ranked, start=1):
            badge = medals[idx - 1] if idx <= 3 else f"{idx}."
            lines.append(
                f"{badge} <b>{p.display_name}</b> — <code>{p.score}/{total_q}</code> pts ({p.total_time:.1f}s)"
            )

        leaderboard_body = "\n".join(lines) if lines else "<i>No participants recorded scores.</i>"

        winner_announcement = ""
        if ranked and ranked[0].score > 0:
            winner_announcement = f"\n\n🎉 <b>Winner:</b> {ranked[0].display_name} with {ranked[0].score} points!"

        return (
            f"🏆 <b>Quiz Battle Finished!</b>\n"
            f"🎯 <b>Quiz:</b> {html.escape(self.title)}\n"
            f"📊 <b>Total Questions:</b> <code>{total_q}</code>\n\n"
            f"<b>Final Leaderboard:</b>\n"
            f"{leaderboard_body}"
            f"{winner_announcement}"
        )


class QuizGameManager:
    """Manages all active group quiz battles across Telegram chats."""

    def __init__(self) -> None:
        self.active_games: dict[int, GameSession] = {}
        self.poll_to_chat: dict[str, int] = {}

    def get_game(self, chat_id: int) -> Optional[GameSession]:
        """Get the active game in a specific chat."""
        return self.active_games.get(chat_id)

    def get_game_by_poll(self, poll_id: str) -> Optional[GameSession]:
        """Find active game corresponding to a poll ID."""
        chat_id = self.poll_to_chat.get(poll_id)
        if chat_id:
            return self.active_games.get(chat_id)
        return None

    def create_game(
        self,
        chat_id: int,
        chat_title: Optional[str],
        quiz_set_id: int,
        title: str,
        description: Optional[str],
        questions: list[QuizQuestion],
        time_limit: int,
        initiator_id: int,
        initiator_name: str,
    ) -> GameSession:
        """Initialize a new game session in lobby state."""
        # Stop existing game if any
        self.stop_game(chat_id)

        session = GameSession(
            chat_id=chat_id,
            chat_title=chat_title,
            quiz_set_id=quiz_set_id,
            title=title,
            description=description,
            questions=questions,
            time_limit=max(5, min(time_limit, 600)),
            status="lobby",
            initiator_id=initiator_id,
            initiator_name=initiator_name,
        )
        # Register host automatically
        session.add_participant(user_id=initiator_id, full_name=initiator_name)
        self.active_games[chat_id] = session
        return session

    def stop_game(self, chat_id: int) -> Optional[GameSession]:
        """Cancel and clean up an active game in a chat."""
        session = self.active_games.pop(chat_id, None)
        if session:
            session.status = "stopped"
            if session.task and not session.task.done():
                session.task.cancel()
            if session.current_poll_id in self.poll_to_chat:
                self.poll_to_chat.pop(session.current_poll_id, None)
        return session

    def remove_game(self, chat_id: int) -> None:
        """Remove finished game from memory."""
        session = self.active_games.pop(chat_id, None)
        if session and session.current_poll_id in self.poll_to_chat:
            self.poll_to_chat.pop(session.current_poll_id, None)


# Global singleton instance
_game_manager = QuizGameManager()


def get_game_manager() -> QuizGameManager:
    """Access singleton game manager."""
    return _game_manager


async def run_game_loop(bot: Bot, game: GameSession) -> None:
    """Asynchronous game execution loop for live group quizzes."""
    manager = get_game_manager()
    total_q = len(game.questions)
    game.status = "running"
    chat_id = game.chat_id

    logger.info("Starting live quiz battle loop for chat %d (%d questions)", chat_id, total_q)

    try:
        for idx, question in enumerate(game.questions):
            if game.status != "running":
                break

            game.current_question_idx = idx
            game.round_correct.clear()
            game.round_all_answers.clear()

            # Format question with bilingual and rich text formatting
            display_q = format_bilingual_question_text(question.display_question)
            formatted_q = format_rich_text_for_telegram(display_q, max_plain_length=300)
            expl_text = (
                format_rich_text_for_telegram(question.explanation, max_plain_length=200)
                if question.explanation
                else None
            )

            # Pre-send validation before poll creation
            for opt_idx, opt in enumerate(question.options):
                opt_letter = chr(ord("A") + opt_idx)
                val_res = validate_option_length(opt)
                if not val_res.valid:
                    logger.error(
                        "OPTION_LENGTH_VALIDATION game_chat=%s question=%d option=%s length=%d limit=%d status=overflow",
                        chat_id,
                        q_idx + 1,
                        opt_letter,
                        val_res.length,
                        val_res.limit,
                    )
                    raise ValueError(
                        f"Game question {q_idx + 1} Option {opt_letter} exceeds Telegram 100-char limit "
                        f"({val_res.length}/{val_res.limit})"
                    )

            # Send native Quiz Poll (is_anonymous=False is required for Telegram to dispatch poll_answer)
            poll_msg = await bot.send_poll(
                chat_id=chat_id,
                question=formatted_q,
                options=question.options,
                type=PollType.QUIZ,
                is_anonymous=False,
                correct_option_id=question.correct_option,
                explanation=expl_text,
                open_period=game.time_limit,
                question_parse_mode=ParseMode.HTML,
                explanation_parse_mode=ParseMode.HTML if expl_text else None,
            )

            game.current_poll_id = poll_msg.poll.id
            game.current_poll_message_id = poll_msg.message_id
            game.question_start_time = time.time()
            manager.poll_to_chat[poll_msg.poll.id] = chat_id

            # Wait for poll duration (or early wake if all joined players have answered)
            start_wait = time.time()
            while time.time() - start_wait < game.time_limit:
                if game.status != "running":
                    break
                # Early proceed if all joined participants have answered
                if len(game.scores) > 0 and len(game.round_all_answers) >= len(game.scores):
                    await asyncio.sleep(1.0)
                    break
                await asyncio.sleep(0.5)

            if game.status != "running":
                break

            # Stop the poll to close it and show explanation
            try:
                await bot.stop_poll(chat_id=chat_id, message_id=poll_msg.message_id)
            except Exception as stop_err:
                logger.debug("stop_poll notice: %s", stop_err)

            # Clean up poll mapping
            manager.poll_to_chat.pop(poll_msg.poll.id, None)

            # Next question starts immediately when timer ends (Requirement 7: no tracking messages in between)

        # Final Leaderboard
        if game.status == "running":
            game.status = "finished"
            leaderboard_text = game.format_leaderboard_text()

            # Post final leaderboard without Play Again (Requirement 8)
            await bot.send_message(
                chat_id=chat_id,
                text=leaderboard_text,
                parse_mode=ParseMode.HTML,
            )

            # Record in DB
            try:
                ranked = game.get_sorted_leaderboard()
                winner = ranked[0] if ranked else None
                with SessionLocal() as db:
                    record_battle_result(
                        db=db,
                        quiz_set_id=game.quiz_set_id,
                        chat_id=chat_id,
                        chat_title=game.chat_title,
                        winner_name=winner.display_name if winner else None,
                        winner_score=winner.score if winner else 0,
                        total_participants=len(game.scores),
                    )
            except Exception as db_err:
                logger.error("Failed to record battle result: %s", db_err)

    except asyncio.CancelledError:
        logger.info("Quiz battle loop cancelled for chat %d", chat_id)
    except Exception as e:
        logger.error("Error in quiz battle loop for chat %d: %s", chat_id, e, exc_info=True)
    finally:
        manager.remove_game(chat_id)
