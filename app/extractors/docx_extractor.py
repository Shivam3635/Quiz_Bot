"""Extractor for Microsoft Word (.docx) documents."""

import io
import re
from typing import Optional
import docx

from app.extractors.base import BaseExtractor, ExtractedQuestion, ExtractionResult
from app.extractors.sheet_extractor import SheetExtractor
from app.parser.parser import BulkQuizParser
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class DocxExtractor(BaseExtractor):
    """Extracts quiz questions from Word (.docx) documents supporting paragraphs and tables."""

    def __init__(self) -> None:
        self.sheet_extractor = SheetExtractor()
        self.parser = BulkQuizParser()

    def extract_from_bytes(self, content: bytes, filename: str) -> ExtractionResult:
        """Parse raw bytes of a .docx file."""
        lower_name = filename.lower()
        if not lower_name.endswith(".docx"):
            return ExtractionResult(
                error_message=f"Unsupported Word document format: {filename}. Please upload .docx files.",
                source_filename=filename,
            )

        try:
            doc = docx.Document(io.BytesIO(content))
        except Exception as e:
            logger.exception("Failed to open Word document %s: %s", filename, e)
            return ExtractionResult(
                error_message=f"Failed to read Word document: {str(e)}",
                source_filename=filename,
            )

        extracted_questions: list[ExtractedQuestion] = []
        raw_text_parts: list[str] = []

        # 1. Process all tables found in the document
        for table_idx, table in enumerate(doc.tables, start=1):
            table_rows: list[list[str]] = []
            for row in table.rows:
                row_cells = [cell.text.strip() for cell in row.cells]
                # Avoid duplicate cell texts caused by merged cells
                cleaned_cells: list[str] = []
                for idx, c in enumerate(row_cells):
                    cleaned_cells.append(c)
                if any(cleaned_cells):
                    table_rows.append(cleaned_cells)

            if table_rows:
                table_result = self.sheet_extractor._process_grid(table_rows, f"{filename}_table_{table_idx}")
                if table_result.success and table_result.questions:
                    extracted_questions.extend(table_result.questions)
                    raw_text_parts.append(table_result.raw_text)

        # 2. Process paragraphs in the document
        paragraph_lines: list[str] = []
        for p in doc.paragraphs:
            text = p.text.strip()
            if text:
                paragraph_lines.append(text)

        if paragraph_lines:
            combined_paragraphs_text = "\n".join(paragraph_lines)
            parse_result = self.parser.parse(combined_paragraphs_text)

            if parse_result.questions:
                # Convert parsed questions into ExtractedQuestion models
                for q_idx, q in enumerate(parse_result.questions, start=len(extracted_questions) + 1):
                    ext_q = ExtractedQuestion(
                        question_text=q.display_question,
                        options=q.options,
                        answer_raw=q.options[q.correct_option] if 0 <= q.correct_option < len(q.options) else None,
                        explanation=q.explanation,
                        question_number=q_idx,
                    )
                    extracted_questions.append(ext_q)

                formatted_blocks = [
                    q.to_formatted_block(default_number=i)
                    for i, q in enumerate(extracted_questions[len(extracted_questions) - len(parse_result.questions):], start=len(extracted_questions) - len(parse_result.questions) + 1)
                ]
                raw_text_parts.append("\n\n".join(formatted_blocks))

        if not extracted_questions:
            return ExtractionResult(
                error_message="Could not detect questions in the Word document. Ensure questions are numbered (e.g. Q1, 1.) with options (A, B, C, D) and an answer, or formatted in a table.",
                source_filename=filename,
            )

        # Build clean sequential blocks for all combined questions
        final_blocks = [q.to_formatted_block(default_number=i) for i, q in enumerate(extracted_questions, start=1)]
        final_raw_text = "\n\n".join(final_blocks)

        return ExtractionResult(
            questions=extracted_questions,
            raw_text=final_raw_text,
            total_found=len(extracted_questions),
            source_filename=filename,
        )

    def extract_from_file(self, file_path: str) -> ExtractionResult:
        """Read and parse from local file path."""
        with open(file_path, "rb") as f:
            content = f.read()
        return self.extract_from_bytes(content, filename=file_path)
