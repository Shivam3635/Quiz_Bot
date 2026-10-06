"""Base interfaces and models for document question extraction."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExtractedQuestion:
    """Represents a question extracted from a document before normalization."""
    question_text: str
    options: list[str] = field(default_factory=list)
    answer_raw: Optional[str] = None
    explanation: Optional[str] = None
    question_number: Optional[int] = None

    @property
    def correct_index(self) -> Optional[int]:
        """Convert answer_raw (e.g. 'A', '1', option text) to 0-based option index."""
        if not self.answer_raw:
            return None
        raw = self.answer_raw.strip().upper()
        # Single letter A-Z
        if len(raw) == 1 and "A" <= raw <= "Z":
            idx = ord(raw) - ord("A")
            if idx < len(self.options):
                return idx
        # Digit 1-9
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(self.options):
                return idx
        # Option text exact or case-insensitive match
        for idx, opt in enumerate(self.options):
            if opt.strip().lower() == self.answer_raw.strip().lower():
                return idx
        return None

    def to_formatted_block(self, default_number: int = 1) -> str:
        """Convert extracted question to standardized QuizBotPro text block."""
        q_num = self.question_number if self.question_number is not None else default_number
        lines = [f"Q{q_num}. {self.question_text.strip()}"]

        # Option keys: A), B), C), D), ...
        option_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        c_idx = self.correct_index
        for idx, opt in enumerate(self.options):
            letter = option_letters[idx] if idx < len(option_letters) else str(idx + 1)
            # If answer is indicated by checkmark already in opt, keep it; otherwise if c_idx matches, can add ✅
            opt_clean = opt.strip()
            if c_idx == idx and not any(m in opt_clean for m in ("✅", "✔️", "✔")):
                lines.append(f"{letter}) {opt_clean} ✅")
            else:
                lines.append(f"{letter}) {opt_clean}")

        if self.answer_raw and c_idx is None:
            lines.append(f"Answer: {self.answer_raw.strip()}")

        if self.explanation:
            lines.append(f"Explanation: {self.explanation.strip()}")

        return "\n".join(lines)


@dataclass
class ExtractionResult:
    """Result of document extraction."""
    questions: list[ExtractedQuestion] = field(default_factory=list)
    raw_text: str = ""
    total_found: int = 0
    error_message: Optional[str] = None
    source_filename: str = ""

    @property
    def success(self) -> bool:
        """Check if extraction succeeded without errors."""
        return self.error_message is None

    @property
    def error(self) -> Optional[str]:
        """Alias for error_message."""
        return self.error_message

    def to_formatted_text(self) -> str:
        """Return formatted raw text suitable for bulk quiz parser."""
        if self.raw_text:
            return self.raw_text
        blocks = [q.to_formatted_block(default_number=i) for i, q in enumerate(self.questions, start=1)]
        return "\n\n".join(blocks)


class BaseExtractor(ABC):
    """Abstract base extractor class."""

    def extract(self, content: bytes, filename: str) -> ExtractionResult:
        """Convenience alias for extract_from_bytes."""
        return self.extract_from_bytes(content, filename)

    @abstractmethod
    def extract_from_bytes(self, content: bytes, filename: str) -> ExtractionResult:
        """Extract questions from raw file bytes."""
        pass

    @abstractmethod
    def extract_from_file(self, file_path: str) -> ExtractionResult:
        """Extract questions from a file path."""
        pass
