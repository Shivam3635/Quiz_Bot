"""Phase 3 Regression Test Suite.

Covers:
1. ZIP archive security limits (entry limits, aggregate size, zip bombs, member limits, path traversal, symlinks, zero compressed size, streaming bounds).
2. Publishing idempotency locking (rapid double-click prevention, lock release on normal and error exit, lock lifecycle across clear_creation_session).
3. Network timeout and API failure handling during batch publishing.
4. Database transaction rollback verification for save_quiz_batch and update_quiz_set_questions.
"""

import io
import stat
import zipfile
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram import Bot, CallbackQuery, Chat, Message, Update, User as TgUser
from telegram.error import TelegramError, TimedOut, RetryAfter

from app.extractors.router import (
    DocumentRouter,
    MAX_ZIP_ENTRIES,
    MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES,
    MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES,
    MAX_COMPRESSION_RATIO,
    is_safe_zip_member,
)
from app.bot.handlers.publish import start_publishing_callback
from app.bot.states import QuizCreationState
from app.database.database import Base
from app.database.models import User, QuizSet, Question, Option
from app.database.repositories import (
    save_quiz_batch,
    update_quiz_set_questions,
    get_or_create_user,
    get_quiz_set_by_id,
)
from app.parser.models import QuizQuestion, QuizSettings
from app.services.publishing_service import PublishingService
from app.services.telegram_service import TelegramService


# ============================================================================
# 1. ZIP Archive Security Regression Tests
# ============================================================================

class TestZipSecurity:
    """Security tests for ZIP archive extraction bounds and traversal guards."""

    @pytest.fixture
    def router(self):
        return DocumentRouter()

    def test_safe_member_detection(self):
        """Verify is_safe_zip_member accepts normal files and rejects traversal attempts."""
        safe_info = zipfile.ZipInfo("quizzes/test.txt")
        is_safe, reason = is_safe_zip_member(safe_info)
        assert is_safe is True
        assert reason is None

        # Path traversal with ..
        dot_dot_info = zipfile.ZipInfo("../secret.txt")
        is_safe, reason = is_safe_zip_member(dot_dot_info)
        assert is_safe is False
        assert "traversal" in reason.lower()

        dot_dot_nested = zipfile.ZipInfo("sub/../../secret.txt")
        is_safe, reason = is_safe_zip_member(dot_dot_nested)
        assert is_safe is False
        assert "traversal" in reason.lower()

        # Absolute paths
        abs_unix = zipfile.ZipInfo("/etc/passwd")
        is_safe, reason = is_safe_zip_member(abs_unix)
        assert is_safe is False
        assert "absolute path" in reason.lower()

        abs_win = zipfile.ZipInfo("C:/Windows/system32/cmd.exe")
        is_safe, reason = is_safe_zip_member(abs_win)
        assert is_safe is False
        assert "absolute path" in reason.lower()

        # Symlinks (Unix S_IFLNK)
        symlink_info = zipfile.ZipInfo("link_to_etc")
        symlink_info.create_system = 3  # Unix
        symlink_info.external_attr = 0o120000 << 16
        is_safe, reason = is_safe_zip_member(symlink_info)
        assert is_safe is False
        assert "symbolic link" in reason.lower()

    def test_normal_valid_zip_ingestion(self, router):
        """Normal valid zip archive with valid questions is extracted successfully."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "math.txt",
                "Q1. What is 2 + 2?\nA. 3\nB. 4 ✅\n\nQ2. What is 3 + 3?\nA. 6 ✅\nB. 5\n",
            )

        buf.seek(0)
        summary = router.ingest(buf.getvalue(), "valid.zip")
        assert summary.success is True
        assert summary.total_detected == 2
        assert "What is 2 + 2?" in summary.raw_text
        assert "What is 3 + 3?" in summary.raw_text

    def test_zip_exceeding_max_entries_rejected(self, router):
        """Archives containing more than MAX_ZIP_ENTRIES files must be rejected."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i in range(MAX_ZIP_ENTRIES + 5):
                zf.writestr(f"file_{i}.txt", f"Q{i}. Question?\nA) 1\nB) 2\nAnswer: A\n")

        buf.seek(0)
        summary = router.ingest(buf.getvalue(), "large_archive.zip")
        assert summary.success is False
        assert summary.error_message is not None
        assert "exceeding the maximum allowed limit" in summary.error_message

    def test_zip_member_uncompressed_size_rejected(self, router):
        """Archive entries exceeding MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES in header must be rejected."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.txt", b"x")

        fake_large = zipfile.ZipInfo("large.txt")
        fake_large.file_size = 25 * 1024 * 1024  # 25MB > 20MB limit
        fake_large.compress_size = 10 * 1024 * 1024

        with patch("zipfile.ZipFile.infolist", return_value=[fake_large]):
            summary = router.ingest(buf.getvalue(), "large_member.zip")
            assert summary.success is False
            assert "exceeds the maximum 20MB limit" in summary.error_message

    def test_zip_exceeding_aggregate_size_rejected(self, router):
        """Archives whose declared uncompressed size exceeds limit must be rejected."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.txt", b"x")

        # 3 entries of 18MB = 54MB (each below the 20MB single-member limit, ratio < 100:1, total > 50MB)
        fake1 = zipfile.ZipInfo("file1.txt")
        fake1.file_size = 18 * 1024 * 1024
        fake1.compress_size = 10 * 1024 * 1024
        fake2 = zipfile.ZipInfo("file2.txt")
        fake2.file_size = 18 * 1024 * 1024
        fake2.compress_size = 10 * 1024 * 1024
        fake3 = zipfile.ZipInfo("file3.txt")
        fake3.file_size = 18 * 1024 * 1024
        fake3.compress_size = 10 * 1024 * 1024

        with patch("zipfile.ZipFile.infolist", return_value=[fake1, fake2, fake3]):
            summary = router.ingest(buf.getvalue(), "huge.zip")
            assert summary.success is False
            assert summary.error_message is not None
            assert "exceeds the maximum allowed limit of 50MB" in summary.error_message

    def test_zip_bomb_ratio_rejected(self, router):
        """Archives with extreme compression ratios (zip bombs) must be aborted."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.txt", b"x")

        fake_bomb = zipfile.ZipInfo("bomb.txt")
        fake_bomb.file_size = 10 * 1024 * 1024  # 10 MB
        fake_bomb.compress_size = 100           # 100 bytes -> ratio 100,000:1 (> 100:1)

        with patch("zipfile.ZipFile.infolist", return_value=[fake_bomb]):
            summary = router.ingest(buf.getvalue(), "bomb.zip")
            assert summary.success is False
            assert summary.error_message is not None
            assert "excessive compression ratio" in summary.error_message

    def test_zip_zero_compressed_size_with_positive_file_size_rejected(self, router):
        """Entries claiming zero compressed size with non-zero file size must be rejected."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.txt", b"x")

        fake_zero_comp = zipfile.ZipInfo("zero_comp.txt")
        fake_zero_comp.file_size = 1024 * 1024  # 1 MB
        fake_zero_comp.compress_size = 0        # 0 bytes compressed -> invalid/anomalous

        with patch("zipfile.ZipFile.infolist", return_value=[fake_zero_comp]):
            summary = router.ingest(buf.getvalue(), "zero_comp.zip")
            assert summary.success is False
            assert summary.error_message is not None
            assert "zero compressed size with non-zero uncompressed size" in summary.error_message

    def test_zip_zero_compressed_size_empty_file_handled_safely(self, router):
        """Legitimate 0-byte entries (0 compressed, 0 uncompressed) are handled safely."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("empty.txt", b"")
            zf.writestr("questions.txt", "Q1. What is 5 x 5?\nA. 25 ✅\nB. 30\n")

        buf.seek(0)
        summary = router.ingest(buf.getvalue(), "with_empty.zip")
        assert summary.success is True
        assert summary.total_detected == 1
        assert "What is 5 x 5?" in summary.raw_text

    def test_zip_actual_decompressed_member_limit_exceeded_during_streaming(self, router):
        """If actual streaming decompressed bytes exceed 20MB for a member, extraction aborts."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.txt", b"x")

        class ChunkStream(io.BytesIO):
            def __init__(self, total_bytes):
                super().__init__(b"A" * total_bytes)

        fake_zinfo = zipfile.ZipInfo("oversized.txt")
        fake_zinfo.file_size = 100  # Lie in header
        fake_zinfo.compress_size = 100

        with patch("zipfile.ZipFile.infolist", return_value=[fake_zinfo]):
            # Stream 21MB in chunks
            with patch("zipfile.ZipFile.open", return_value=ChunkStream(21 * 1024 * 1024)):
                summary = router.ingest(buf.getvalue(), "stream_bomb.zip")
                assert summary.success is False
                assert summary.error_message is not None
                assert "decompressed size exceeded the maximum allowed limit of 20MB" in summary.error_message

    def test_zip_actual_decompressed_aggregate_limit_exceeded_during_streaming(self, router):
        """If aggregate streaming decompressed bytes exceed 50MB across members, extraction aborts."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.txt", b"x")

        class ChunkStream(io.BytesIO):
            def __init__(self, total_bytes):
                super().__init__(b"B" * total_bytes)

        fake1 = zipfile.ZipInfo("file1.txt")
        fake1.file_size = 100
        fake1.compress_size = 100

        fake2 = zipfile.ZipInfo("file2.txt")
        fake2.file_size = 100
        fake2.compress_size = 100

        fake3 = zipfile.ZipInfo("file3.txt")
        fake3.file_size = 100
        fake3.compress_size = 100

        # Each member yields 18MB (<= 20MB member limit); 3 x 18MB = 54MB > 50MB aggregate limit
        streams = [
            ChunkStream(18 * 1024 * 1024),
            ChunkStream(18 * 1024 * 1024),
            ChunkStream(18 * 1024 * 1024),
        ]

        with patch("zipfile.ZipFile.infolist", return_value=[fake1, fake2, fake3]):
            with patch("zipfile.ZipFile.open", side_effect=streams):
                summary = router.ingest(buf.getvalue(), "aggregate_bomb.zip")
                assert summary.success is False
                assert summary.error_message is not None
                assert "Total decompressed size exceeded safety limit of 50MB" in summary.error_message

    def test_zip_with_traversal_member_skipped(self, router):
        """Path traversal members inside a zip must be skipped during extraction."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("../etc_passwd.txt", "root:x:0:0:")
            zf.writestr("valid.txt", "Q1. What is water?\nA. Liquid ✅\nB. Solid\n")

        buf.seek(0)
        summary = router.ingest(buf.getvalue(), "mixed.zip")
        assert summary.success is True
        assert summary.total_detected == 1
        assert "What is water?" in summary.raw_text
        assert "root:x" not in summary.raw_text


# ============================================================================
# 2. Publishing Idempotency Lock Regression Tests
# ============================================================================

class TestPublishingIdempotency:
    """Verifies prevention of duplicate publishing from rapid button clicks and lifecycle management."""

    def _create_mock_update_and_context(self, user_data=None):
        update = MagicMock(spec=Update)
        update.callback_query = MagicMock(spec=CallbackQuery)
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        update.callback_query.message = MagicMock(spec=Message)
        update.callback_query.message.chat_id = 98765
        update.effective_user = MagicMock(spec=TgUser)
        update.effective_user.id = 12345
        update.effective_user.username = "quiz_author"
        update.effective_chat = MagicMock(spec=Chat)
        update.effective_chat.id = 98765

        context = MagicMock()
        context.user_data = user_data if user_data is not None else {}
        context.bot = MagicMock(spec=Bot)
        return update, context

    def test_clear_creation_session_does_not_release_publishing_lock(self):
        """clear_creation_session must clear draft data but NOT prematurely release an active publishing lock."""
        from app.bot.handlers.bulk import clear_creation_session

        update, context = self._create_mock_update_and_context(
            user_data={
                "is_publishing": True,
                "bulk_questions": ["Q1"],
                "quiz_settings": QuizSettings(),
                "active_session_id": "test_session",
            }
        )

        clear_creation_session(context, user_id=12345, chat_id=98765)

        # Draft keys are cleared
        assert "bulk_questions" not in context.user_data
        assert "quiz_settings" not in context.user_data
        assert "active_session_id" not in context.user_data
        # Publishing lock remains ACTIVE
        assert context.user_data.get("is_publishing") is True

    @pytest.mark.asyncio
    async def test_concurrent_click_blocked_by_idempotency_lock(self):
        """When is_publishing is True, subsequent clicks are rejected immediately."""
        update, context = self._create_mock_update_and_context(
            user_data={"is_publishing": True}
        )

        state = await start_publishing_callback(update, context)

        assert state == QuizCreationState.CONFIRMING_PUBLISH
        update.callback_query.answer.assert_awaited_once_with(
            "⚠️ Publishing is already in progress. Please wait.", show_alert=True
        )
        # Verify edit_message_text was never called (no publish started)
        update.callback_query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publishing_lock_lifecycle_active_during_execution_and_released_in_finally(self):
        """is_publishing remains True throughout execution (even after clear_creation_session),
        blocking concurrent calls, and is released only by the outer finally block."""
        sample_q = [
            QuizQuestion(
                question="What is 2+2?",
                options=["3", "4"],
                correct_option=1,
            )
        ]
        update, context = self._create_mock_update_and_context(
            user_data={
                "bulk_questions": sample_q,
                "quiz_settings": QuizSettings(),
            }
        )

        lock_status_during_publish = None
        concurrent_call_state = None

        async def fake_publish_batch(*args, **kwargs):
            nonlocal lock_status_during_publish, concurrent_call_state
            # Check lock is held while publish_batch executes
            lock_status_during_publish = context.user_data.get("is_publishing")

            # Simulate a concurrent incoming callback during active publishing
            second_update, second_context = self._create_mock_update_and_context(
                user_data=context.user_data
            )
            concurrent_call_state = await start_publishing_callback(second_update, second_context)

            from app.services.publishing_service import PublishingSummary
            return PublishingSummary(
                total=1,
                successful=1,
                failed=0,
                failed_items=[],
            )

        with patch("app.bot.handlers.publish.publishing_service.publish_batch", side_effect=fake_publish_batch):
            state = await start_publishing_callback(update, context)

            assert state == -1  # ConversationHandler.END
            # Lock was verified active while publishing was running
            assert lock_status_during_publish is True
            # Concurrent callback was rejected because lock was held
            assert concurrent_call_state == QuizCreationState.CONFIRMING_PUBLISH
            # Lock is released after outer finally block completes
            assert "is_publishing" not in context.user_data

    @pytest.mark.asyncio
    async def test_idempotency_lock_cleared_after_successful_publish(self):
        """Lock is set during execution and removed when publishing completes."""
        sample_q = [
            QuizQuestion(
                question="What is 2+2?",
                options=["3", "4"],
                correct_option=1,
            )
        ]
        update, context = self._create_mock_update_and_context(
            user_data={
                "bulk_questions": sample_q,
                "quiz_settings": QuizSettings(),
            }
        )

        with patch("app.bot.handlers.publish.publishing_service.publish_batch") as mock_pub:
            from app.services.publishing_service import PublishingSummary
            mock_pub.return_value = PublishingSummary(
                total=1,
                successful=1,
                failed=0,
                failed_items=[],
            )

            state = await start_publishing_callback(update, context)

            assert state == -1  # ConversationHandler.END
            assert "is_publishing" not in context.user_data

    @pytest.mark.asyncio
    async def test_idempotency_lock_cleared_on_unexpected_exception(self):
        """Lock is reliably released even if an unhandled exception occurs."""
        sample_q = [
            QuizQuestion(
                question="Crash test?",
                options=["A", "B"],
                correct_option=0,
            )
        ]
        update, context = self._create_mock_update_and_context(
            user_data={
                "bulk_questions": sample_q,
                "quiz_settings": QuizSettings(),
            }
        )

        with patch("app.bot.handlers.publish.publishing_service.publish_batch", side_effect=RuntimeError("Unexpected crash")):
            with pytest.raises(RuntimeError, match="Unexpected crash"):
                await start_publishing_callback(update, context)

            # Verification: lock must be removed even after exception
            assert "is_publishing" not in context.user_data


# ============================================================================
# 3. Network Timeout and Telegram API Failure Regression Tests
# ============================================================================

class TestPublishingNetworkFailures:
    """Verifies resilient handling of network timeouts, flood control and API errors."""

    @pytest.fixture
    def sample_questions(self):
        return [
            QuizQuestion(question="Q1?", options=["A", "B"], correct_option=0),
            QuizQuestion(question="Q2?", options=["C", "D"], correct_option=1),
            QuizQuestion(question="Q3?", options=["E", "F"], correct_option=0),
        ]

    @pytest.fixture
    def settings(self):
        return QuizSettings(is_anonymous=True, header_banner_enabled=False)

    @pytest.mark.asyncio
    async def test_network_timeout_handling(self, sample_questions, settings):
        """Network timeout on one question must not abort remaining questions."""
        mock_telegram = MagicMock(spec=TelegramService)
        # Q1 succeeds, Q2 times out, Q3 succeeds
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                True,
                TimedOut("Connection to Telegram timed out"),
                True,
            ]
        )

        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.001

        mock_bot = MagicMock(spec=Bot)
        summary = await service.publish_batch(
            bot=mock_bot,
            target_chat_id=12345,
            questions=sample_questions,
            settings=settings,
        )

        assert summary.total == 3
        assert summary.successful == 2
        assert summary.failed == 1
        assert not summary.is_complete_success
        assert len(summary.failed_items) == 1
        assert summary.failed_items[0].index == 2
        assert "timed out" in summary.failed_items[0].error_message.lower()

    @pytest.mark.asyncio
    async def test_retry_after_flood_control(self, sample_questions, settings):
        """RetryAfter flood control wait is honored and retried once."""
        mock_telegram = MagicMock(spec=TelegramService)
        # Q1 triggers RetryAfter(0), then succeeds on retry
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                RetryAfter(0),
                True,  # retry succeeds
                True,  # Q2
                True,  # Q3
            ]
        )

        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.001

        mock_bot = MagicMock(spec=Bot)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            summary = await service.publish_batch(
                bot=mock_bot,
                target_chat_id=12345,
                questions=sample_questions,
                settings=settings,
            )

        assert summary.successful == 3
        assert summary.failed == 0
        assert summary.is_complete_success


# ============================================================================
# 4. Database Transaction Rollback Regression Tests
# ============================================================================

class TestDatabaseRollback:
    """Verifies that database failures trigger explicit rollbacks to maintain integrity."""

    @pytest.fixture
    def test_db(self):
        """In-memory SQLite database session fixture."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            yield session
        finally:
            session.close()

    def test_save_quiz_batch_rolls_back_on_failure(self, test_db):
        """When an exception occurs inside save_quiz_batch, rollback is called."""
        questions = [
            QuizQuestion(question="Valid Q", options=["A", "B"], correct_option=0)
        ]
        settings = QuizSettings()

        user = get_or_create_user(test_db, telegram_id=55555, username="rollback_tester")

        # Simulate an error during question persistence by mocking flush to fail on second call
        original_flush = test_db.flush
        flush_call_count = 0

        def failing_flush():
            nonlocal flush_call_count
            flush_call_count += 1
            if flush_call_count > 1:
                raise RuntimeError("Simulated DB flush crash")
            original_flush()

        with patch.object(test_db, "flush", side_effect=failing_flush):
            with patch.object(test_db, "rollback", wraps=test_db.rollback) as mock_rollback:
                with pytest.raises(RuntimeError, match="Simulated DB flush crash"):
                    save_quiz_batch(
                        db=test_db,
                        user_id=user.id,
                        questions=questions,
                        settings=settings,
                        title="Will Fail",
                    )
                # Verification: rollback must be explicitly called
                mock_rollback.assert_called_once()

    def test_update_quiz_set_questions_rolls_back_on_failure(self, test_db):
        """When update_quiz_set_questions fails, previous questions are preserved via rollback."""
        questions_initial = [
            QuizQuestion(question="Initial Q1", options=["A", "B"], correct_option=0)
        ]
        settings = QuizSettings()

        user = get_or_create_user(test_db, telegram_id=66666, username="updater_test")
        quiz_set = save_quiz_batch(
            db=test_db,
            user_id=user.id,
            questions=questions_initial,
            settings=settings,
            title="Update Rollback Test",
        )
        initial_set_id = quiz_set.id
        assert len(quiz_set.questions) == 1

        new_questions = [
            QuizQuestion(question="New Q1", options=["1", "2"], correct_option=0),
            QuizQuestion(question="New Q2", options=["3", "4"], correct_option=1),
        ]

        def failing_commit():
            raise RuntimeError("Simulated DB commit crash")

        with patch.object(test_db, "commit", side_effect=failing_commit):
            with patch.object(test_db, "rollback", wraps=test_db.rollback) as mock_rollback:
                with pytest.raises(RuntimeError, match="Simulated DB commit crash"):
                    update_quiz_set_questions(
                        db=test_db,
                        quiz_set_id=initial_set_id,
                        questions=new_questions,
                    )
                mock_rollback.assert_called_once()

        # In test_db, verify the initial question is still intact
        reloaded = get_quiz_set_by_id(test_db, initial_set_id)
        assert reloaded is not None
        assert len(reloaded.questions) == 1
        assert reloaded.questions[0].question == "Initial Q1"
