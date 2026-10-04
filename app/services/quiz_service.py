"""High-level service coordinating parsing, validation, and session management."""

from dataclasses import dataclass
from typing import Optional

from app.parser.models import ParsedBatch, QuizQuestion, QuizSettings
from app.parser.parser import QuizBotProParser
from app.parser.validator import QuizValidator, ValidationResult
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class ProcessBatchResult:
    """Outcome of parsing and validating raw text."""

    raw_batch: ParsedBatch
    validation: ValidationResult
    questions: list[QuizQuestion]
    is_ready_for_publish: bool


class QuizService:
    """Coordinates parsing, validation, and preparation of quiz sets."""

    def __init__(
        self,
        parser: Optional[QuizBotProParser] = None,
        validator: Optional[QuizValidator] = None,
    ):
        self.parser = parser or QuizBotProParser()
        self.validator = validator or QuizValidator(allow_extended_options=True)

    def process_raw_text(self, raw_text: str) -> ProcessBatchResult:
        """Parse raw text and validate all extracted questions."""
        parsed_batch = self.parser.parse(raw_text)

        if parsed_batch.has_errors or not parsed_batch.questions:
            # If parsing failed or empty, validation can be skipped or empty
            val_res = ValidationResult(
                total_questions=0,
                valid_questions=[],
                errors=[],
            )
            return ProcessBatchResult(
                raw_batch=parsed_batch,
                validation=val_res,
                questions=[],
                is_ready_for_publish=False,
            )

        # Validate successfully parsed questions against Telegram limits (allowing extended options)
        validation_result = self.validator.validate_batch(parsed_batch.questions, allow_extended_options=True)

        is_ready = (
            not parsed_batch.has_errors
            and validation_result.is_valid
            and len(validation_result.valid_questions) > 0
        )

        return ProcessBatchResult(
            raw_batch=parsed_batch,
            validation=validation_result,
            questions=validation_result.valid_questions,
            is_ready_for_publish=is_ready,
        )
