"""Validation rules and limits for Telegram Quiz Polls."""

from dataclasses import dataclass, field
from typing import Optional
from app.config.settings import (
    TELEGRAM_OPTION_MAX_LENGTH,
    TELEGRAM_QUESTION_MAX_LENGTH,
    TELEGRAM_EXPLANATION_MAX_LENGTH,
)
from app.parser.models import QuizQuestion
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Telegram Poll API constraints
TELEGRAM_MAX_QUESTION_LENGTH = TELEGRAM_QUESTION_MAX_LENGTH
TELEGRAM_MAX_OPTION_LENGTH = TELEGRAM_OPTION_MAX_LENGTH
TELEGRAM_MAX_EXPLANATION_LENGTH = TELEGRAM_EXPLANATION_MAX_LENGTH
TELEGRAM_MIN_OPTIONS = 2
TELEGRAM_MAX_OPTIONS = 10


@dataclass
class OptionLengthResult:
    """Detailed character-length measurement of a quiz option against Telegram constraints."""

    valid: bool
    length: int
    limit: int
    excess: int
    option_text: str

    def to_dict(self) -> dict:
        """Return dict representation as specified in design requirements."""
        return {
            "valid": self.valid,
            "length": self.length,
            "limit": self.limit,
            "excess": self.excess,
        }

    def __getitem__(self, item: str):
        return self.to_dict()[item]


def validate_option_length(
    option_text: str,
    limit: int = TELEGRAM_OPTION_MAX_LENGTH,
) -> OptionLengthResult:
    """
    Validate a single quiz option against Telegram's character limit.
    Treats limit as Unicode character count (not word or byte count).
    Supports Hindi/Devanagari, English, punctuation, emojis, parentheses, etc.
    """
    cleaned = option_text.strip()
    length = len(cleaned)
    excess = max(0, length - limit)
    is_valid = 0 < length <= limit
    return OptionLengthResult(
        valid=is_valid,
        length=length,
        limit=limit,
        excess=excess,
        option_text=cleaned,
    )


@dataclass
class QuestionValidationError:
    """Represents a specific validation issue on a question."""

    question_index: int
    rule: str
    message: str
    option_index: Optional[int] = None
    option_letter: Optional[str] = None
    length: Optional[int] = None
    limit: Optional[int] = None
    excess: Optional[int] = None


@dataclass
class ValidationResult:
    """Consolidated outcome of batch validation."""

    total_questions: int
    valid_questions: list[QuizQuestion] = field(default_factory=list)
    errors: list[QuestionValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Return True if all questions passed validation with zero errors."""
        return len(self.errors) == 0 and len(self.valid_questions) > 0

    @property
    def valid_count(self) -> int:
        """Count of valid questions."""
        return len(self.valid_questions)

    @property
    def error_count(self) -> int:
        """Count of failed questions."""
        return len(self.errors)

    @property
    def failed_question_indices(self) -> list[int]:
        """Return sorted 1-based indices of questions that have errors."""
        return sorted(list(set(e.question_index for e in self.errors)))

    @property
    def has_oversized_options(self) -> bool:
        """Return True if any options exceeded Telegram's 100-character limit."""
        return any(e.rule == "OPTION_TOO_LONG" for e in self.errors)

    @property
    def oversized_option_errors(self) -> list[QuestionValidationError]:
        """Return list of errors specifically regarding oversized options."""
        return [e for e in self.errors if e.rule == "OPTION_TOO_LONG"]

    def format_oversized_options_summary(self) -> str:
        """Format clean list of oversized options for Telegram UI."""
        lines = []
        for err in self.oversized_option_errors:
            q_num = err.question_index
            opt_letter = err.option_letter or (chr(ord("A") + err.option_index) if err.option_index is not None else "?")
            length = err.length if err.length is not None else "?"
            limit = err.limit if err.limit is not None else TELEGRAM_OPTION_MAX_LENGTH
            lines.append(f"• <b>Q{q_num} → Option {opt_letter}:</b> <code>{length}/{limit}</code>")
        return "\n".join(lines)

    def get_formatted_error_report(self, max_display: int = 5, custom_footer: Optional[str] = None) -> str:
        """Generate a user-friendly error summary suitable for Telegram display."""
        if not self.errors:
            return "✅ All questions passed validation!"

        unique_failed_q = len(set(e.question_index for e in self.errors))
        lines = [
            f"❌ <b>{unique_failed_q} question(s) need attention.</b>\n"
        ]

        # Group errors by question index
        grouped: dict[int, list[str]] = {}
        for err in self.errors:
            grouped.setdefault(err.question_index, []).append(err.message)

        displayed_count = 0
        for q_idx, msgs in grouped.items():
            if displayed_count >= max_display:
                remaining = len(grouped) - max_display
                lines.append(f"<i>... and {remaining} more question(s) with errors.</i>")
                break

            lines.append(f"<b>Question {q_idx}:</b>")
            for msg in msgs:
                lines.append(f"• {msg}")
            lines.append("")
            displayed_count += 1

        if custom_footer is not None:
            if custom_footer:
                lines.append(custom_footer)
        else:
            lines.append("Please fix these issues and send the batch again.")
        return "\n".join(lines)


class QuizValidator:
    """Validates QuizQuestion models against Telegram Bot API poll constraints."""

    def validate_single(self, q: QuizQuestion, index: int = 1) -> list[QuestionValidationError]:
        """Validate a single question and return all violation errors."""
        errors: list[QuestionValidationError] = []

        # 1. Question text length
        q_len = len(q.question)
        if q_len == 0:
            errors.append(
                QuestionValidationError(
                    question_index=index,
                    rule="EMPTY_QUESTION",
                    message="Question text cannot be empty.",
                )
            )
        elif q_len > TELEGRAM_MAX_QUESTION_LENGTH:
            errors.append(
                QuestionValidationError(
                    question_index=index,
                    rule="QUESTION_TOO_LONG",
                    message=(
                        f"Question text is {q_len} characters (Telegram limit is "
                        f"{TELEGRAM_MAX_QUESTION_LENGTH} characters)."
                    ),
                )
            )

        # 2. Options count
        opt_count = len(q.options)
        if opt_count < TELEGRAM_MIN_OPTIONS:
            errors.append(
                QuestionValidationError(
                    question_index=index,
                    rule="TOO_FEW_OPTIONS",
                    message=f"Only {opt_count} option(s) provided. Minimum is {TELEGRAM_MIN_OPTIONS}.",
                )
            )
        elif opt_count > TELEGRAM_MAX_OPTIONS:
            errors.append(
                QuestionValidationError(
                    question_index=index,
                    rule="TOO_MANY_OPTIONS",
                    message=f"{opt_count} options provided. Telegram supports up to {TELEGRAM_MAX_OPTIONS}.",
                )
            )

        # 3. Individual option validation & duplicate detection
        seen_options: set[str] = set()
        for opt_idx, opt in enumerate(q.options):
            opt_letter = chr(ord("A") + opt_idx)
            opt_res = validate_option_length(opt, limit=TELEGRAM_OPTION_MAX_LENGTH)

            if opt_res.length == 0:
                logger.warning(
                    "OPTION_LENGTH_VALIDATION question=%s option=%s length=0 limit=%d status=empty",
                    index,
                    opt_letter,
                    opt_res.limit,
                )
                errors.append(
                    QuestionValidationError(
                        question_index=index,
                        rule="EMPTY_OPTION",
                        message=f"Option {opt_letter} ({opt_idx + 1}) is empty.",
                        option_index=opt_idx,
                        option_letter=opt_letter,
                        length=0,
                        limit=opt_res.limit,
                        excess=0,
                    )
                )
            elif not opt_res.valid:
                logger.warning(
                    "OPTION_LENGTH_VALIDATION question=%s option=%s length=%d limit=%d excess=%d status=overflow",
                    index,
                    opt_letter,
                    opt_res.length,
                    opt_res.limit,
                    opt_res.excess,
                )
                errors.append(
                    QuestionValidationError(
                        question_index=index,
                        rule="OPTION_TOO_LONG",
                        message=(
                            f"Option {opt_letter} is {opt_res.length} characters "
                            f"(limit: {opt_res.limit}, exceeds by {opt_res.excess})."
                        ),
                        option_index=opt_idx,
                        option_letter=opt_letter,
                        length=opt_res.length,
                        limit=opt_res.limit,
                        excess=opt_res.excess,
                    )
                )
            else:
                logger.debug(
                    "OPTION_LENGTH_VALIDATION question=%s option=%s length=%d limit=%d status=valid",
                    index,
                    opt_letter,
                    opt_res.length,
                    opt_res.limit,
                )

            # Check for duplicate options (case-insensitive)
            normalized_opt = opt_res.option_text.lower()
            if normalized_opt in seen_options:
                errors.append(
                    QuestionValidationError(
                        question_index=index,
                        rule="DUPLICATE_OPTION",
                        message=f"Duplicate option detected: \"{opt_res.option_text}\". Options must be distinct.",
                        option_index=opt_idx,
                        option_letter=opt_letter,
                    )
                )
            seen_options.add(normalized_opt)

        # 4. Correct answer bounds
        if q.correct_option < 0 or q.correct_option >= opt_count:
            errors.append(
                QuestionValidationError(
                    question_index=index,
                    rule="INVALID_CORRECT_ANSWER",
                    message=(
                        f"Correct answer index {q.correct_option} is out of bounds for "
                        f"{opt_count} options."
                    ),
                )
            )

        # 5. Explanation length
        if q.explanation:
            expl_len = len(q.explanation)
            if expl_len > TELEGRAM_MAX_EXPLANATION_LENGTH:
                errors.append(
                    QuestionValidationError(
                        question_index=index,
                        rule="EXPLANATION_TOO_LONG",
                        message=(
                            f"Explanation is {expl_len} characters "
                            f"(Telegram limit is {TELEGRAM_MAX_EXPLANATION_LENGTH})."
                        ),
                    )
                )

        return errors

    def validate_batch(self, questions: list[QuizQuestion]) -> ValidationResult:
        """Validate an entire list of questions and segregate valid vs invalid."""
        valid_list: list[QuizQuestion] = []
        all_errors: list[QuestionValidationError] = []

        for idx, q in enumerate(questions, start=1):
            q_errors = self.validate_single(q, index=idx)
            if q_errors:
                all_errors.extend(q_errors)
            else:
                valid_list.append(q)

        return ValidationResult(
            total_questions=len(questions),
            valid_questions=valid_list,
            errors=all_errors,
        )
