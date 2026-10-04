"""Validation rules and limits for Telegram Quiz Polls."""

from dataclasses import dataclass, field
from typing import Optional
from app.parser.models import QuizQuestion
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Telegram Poll API constraints
TELEGRAM_MAX_QUESTION_LENGTH = 300
TELEGRAM_MAX_OPTION_LENGTH = 100
TELEGRAM_MAX_EXTENDED_OPTION_LENGTH = 1000
TELEGRAM_MAX_EXPLANATION_LENGTH = 200
TELEGRAM_MIN_OPTIONS = 2
TELEGRAM_MAX_OPTIONS = 10


@dataclass
class QuestionValidationError:
    """Represents a specific validation issue on a question."""

    question_index: int
    rule: str
    message: str


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

    def __init__(self, allow_extended_options: bool = False):
        self.allow_extended_options = allow_extended_options

    def validate_single(
        self,
        q: QuizQuestion,
        index: int = 1,
        allow_extended_options: Optional[bool] = None,
    ) -> list[QuestionValidationError]:
        """Validate a single question and return all violation errors."""
        errors: list[QuestionValidationError] = []
        ext_allowed = self.allow_extended_options if allow_extended_options is None else allow_extended_options
        max_opt_len = TELEGRAM_MAX_EXTENDED_OPTION_LENGTH if ext_allowed else TELEGRAM_MAX_OPTION_LENGTH

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
            stripped_opt = opt.strip()
            if not stripped_opt:
                errors.append(
                    QuestionValidationError(
                        question_index=index,
                        rule="EMPTY_OPTION",
                        message=f"Option {opt_idx + 1} is empty.",
                    )
                )
            elif len(stripped_opt) > max_opt_len:
                errors.append(
                    QuestionValidationError(
                        question_index=index,
                        rule="OPTION_TOO_LONG",
                        message=(
                            f"Option {opt_idx + 1} is {len(stripped_opt)} characters "
                            f"(limit: {max_opt_len})."
                        ),
                    )
                )

            # Check for duplicate options (case-insensitive)
            normalized_opt = stripped_opt.lower()
            if normalized_opt in seen_options:
                errors.append(
                    QuestionValidationError(
                        question_index=index,
                        rule="DUPLICATE_OPTION",
                        message=f"Duplicate option detected: \"{stripped_opt}\". Options must be distinct.",
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

    def validate_batch(
        self,
        questions: list[QuizQuestion],
        allow_extended_options: Optional[bool] = None,
    ) -> ValidationResult:
        """Validate an entire list of questions and segregate valid vs invalid."""
        valid_list: list[QuizQuestion] = []
        all_errors: list[QuestionValidationError] = []

        for idx, q in enumerate(questions, start=1):
            q_errors = self.validate_single(
                q,
                index=idx,
                allow_extended_options=allow_extended_options,
            )
            if q_errors:
                all_errors.extend(q_errors)
            else:
                valid_list.append(q)

        return ValidationResult(
            total_questions=len(questions),
            valid_questions=valid_list,
            errors=all_errors,
        )
