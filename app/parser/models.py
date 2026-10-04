import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class QuizQuestion(BaseModel):
    """Represents a single quiz question with options and correct answer."""

    question: str = Field(..., min_length=1, description="The question text")
    options: list[str] = Field(..., min_length=2, description="List of option strings")
    correct_option: int = Field(..., ge=0, description="0-based index of correct option (A=0, B=1, ...)")
    explanation: Optional[str] = Field(None, description="Optional explanation for the correct answer")
    question_number: Optional[int] = Field(None, description="Detected or assigned question number (1, 2, ...)")
    raw_prefix: Optional[str] = Field(None, description="Original prefix as provided, e.g. 'Q1.', 'Q2.', '1.', etc.")

    @property
    def correct_letter(self) -> str:
        """Return the capital letter corresponding to the correct option index."""
        if 0 <= self.correct_option < 26:
            return chr(ord("A") + self.correct_option)
        return str(self.correct_option + 1)

    @property
    def display_question(self) -> str:
        """
        Return the question with its respective question number prefix (e.g. 'Q1.', 'Q2.').
        Preserves the exact prefix as provided during quiz creation (e.g. 'Q1.', '1.').
        """
        from app.utils.helpers import format_bilingual_question_text

        # If question text already begins with a numbering prefix, avoid duplicating
        if re.match(r"^(?:(?:Q|Question|प्रश्न)\s*\d+|\d+)[\.\)\:\-\s]", self.question, re.IGNORECASE):
            return format_bilingual_question_text(self.question)

        # Determine prefix to prepend
        prefix = ""
        if self.raw_prefix:
            prefix = self.raw_prefix.strip()
            # If prefix is just "Q1" or "1", append "."
            if not prefix.endswith((".", ":", ")", "-", " ")):
                prefix += "."
        elif self.question_number:
            prefix = f"Q{self.question_number}."

        if prefix:
            return format_bilingual_question_text(f"{prefix} {self.question}")
        return format_bilingual_question_text(self.question)

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, v: str) -> str:
        """Strip whitespace and format bilingual question text."""
        if isinstance(v, str):
            from app.utils.helpers import format_bilingual_question_text
            return format_bilingual_question_text(v.strip())
        return v

    @field_validator("options", mode="before")
    @classmethod
    def clean_options(cls, v: list) -> list:
        """Strip whitespace from all options."""
        if isinstance(v, list):
            return [str(opt).strip() for opt in v]
        return v


class QuizSettings(BaseModel):
    """Batch settings applied uniformly to all quiz questions."""

    title: Optional[str] = Field(default=None, description="Optional title/name of the quiz batch")
    description: Optional[str] = Field(default=None, description="Optional description of the quiz batch")
    header_banner_enabled: bool = Field(default=True, description="Whether to send a header banner before the quiz starts")
    is_anonymous: bool = Field(default=True, description="Whether poll is anonymous")
    shuffle_options: bool = Field(default=False, description="Whether options are shuffled (where supported)")
    explanation_enabled: bool = Field(default=True, description="Whether explanations are shown to users")
    time_limit: Optional[int] = Field(default=None, description="Open period in seconds (e.g. 15, 30, 45, 60)")
    channel_id: Optional[str] = Field(default=None, description="Target channel username or chat ID")


class ParseError(BaseModel):
    """Represents a parsing error for a specific question block."""

    question_number: Optional[int] = Field(None, description="Detected question number (e.g., 1, 2, ...)")
    raw_header: Optional[str] = Field(None, description="Snippet of question header")
    message: str = Field(..., description="Description of the error")
    details: Optional[str] = Field(None, description="Extra context, e.g., available options")


class ParsedBatch(BaseModel):
    """Result of parsing bulk question text."""

    questions: list[QuizQuestion] = Field(default_factory=list)
    errors: list[ParseError] = Field(default_factory=list)
    total_blocks_found: int = Field(default=0)

    @property
    def has_errors(self) -> bool:
        """Return True if any parsing errors were encountered."""
        return len(self.errors) > 0

    @property
    def is_empty(self) -> bool:
        """Return True if no questions could be parsed and no errors were produced."""
        return len(self.questions) == 0 and len(self.errors) == 0

    @property
    def valid_count(self) -> int:
        """Return count of successfully parsed questions."""
        return len(self.questions)

    @property
    def error_count(self) -> int:
        """Return count of parse errors."""
        return len(self.errors)
