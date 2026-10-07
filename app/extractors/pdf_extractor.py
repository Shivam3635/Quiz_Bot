"""Extractor for PDF (.pdf) documents."""

import io
import re
from typing import Optional
import pypdf

from app.extractors.base import BaseExtractor, ExtractedQuestion, ExtractionResult
from app.parser.parser import BulkQuizParser
from app.utils.devanagari_cleaner import clean_devanagari_text
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Header/Footer artifacts regex (e.g. "Page 1 of 10", "- 1 -", "Page 2")
PAGE_ARTIFACT_RE = re.compile(
    r"^(?:page\s*\d+(?:\s*(?:of|\/)\s*\d+)?|\-+\s*\d+\s*\-+|\d+\s*(?:of|\/)\s*\d+|\[\s*page\s*\d+\s*\])$",
    re.IGNORECASE,
)

# Promotional links, channels, watermarks, exam titles, and footer noise regex
PDF_PROMO_NOISE_RE = re.compile(
    r"(?:https?://\S+|www\.\S+|YouTube\s*(?:Channel)?|Instagram|Telegram|@\w+|Share\s*with\s*Your|Proud\s*NCC|NCC\s+[ABC\s&,–\-]+Certificate|Environment\s*&\s*Ecology|Important\s*MCQs)",
    re.IGNORECASE,
)


class PdfExtractor(BaseExtractor):
    """Extracts quiz questions from PDF (.pdf) files using text layout extraction."""

    def __init__(self) -> None:
        self.parser = BulkQuizParser()

    def extract_from_bytes(self, content: bytes, filename: str) -> ExtractionResult:
        """Parse raw bytes of a .pdf file."""
        lower_name = filename.lower()
        if not lower_name.endswith(".pdf"):
            return ExtractionResult(
                error_message=f"Unsupported PDF document format: {filename}. Please upload .pdf files.",
                source_filename=filename,
            )

        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
        except Exception as e:
            logger.exception("Failed to open PDF %s: %s", filename, e)
            return ExtractionResult(
                error_message=f"Failed to read PDF file: {str(e)}",
                source_filename=filename,
            )

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return ExtractionResult(
                    error_message=f"The PDF '{filename}' is password protected. Please decrypt or unlock the PDF and try again.",
                    source_filename=filename,
                )

        total_pages = len(reader.pages)
        if total_pages == 0:
            return ExtractionResult(
                error_message="The PDF file contains 0 pages.",
                source_filename=filename,
            )

        extracted_page_lines: list[str] = []
        for p_idx, page in enumerate(reader.pages, start=1):
            try:
                raw_extracted = page.extract_text() or ""
                page_text = clean_devanagari_text(raw_extracted)
            except Exception as page_err:
                logger.warning("Error extracting text from page %d of %s: %s", p_idx, filename, page_err)
                continue

            lines = [line.strip() for line in page_text.splitlines() if line.strip()]
            for line in lines:
                # Filter out pure page numbering headers/footers
                if PAGE_ARTIFACT_RE.match(line):
                    continue
                # Filter out promotional URLs, YouTube/Instagram links, and watermarks
                if PDF_PROMO_NOISE_RE.search(line):
                    continue
                extracted_page_lines.append(line)

        if not extracted_page_lines:
            return ExtractionResult(
                error_message=(
                    "No selectable text found in the PDF. "
                    "If this is a scanned document (image-only), please save it as searchable text, Word (.docx), or Excel (.xlsx)."
                ),
                source_filename=filename,
            )

        combined_text = "\n".join(extracted_page_lines)
        parse_result = self.parser.parse(combined_text)

        if not parse_result.questions:
            return ExtractionResult(
                error_message=(
                    "Could not detect questions in the PDF. "
                    "Please ensure questions are formatted with question numbers (Q1., 1.) and options (A, B, C, D) with marked answers."
                ),
                source_filename=filename,
            )

        extracted_questions: list[ExtractedQuestion] = []
        for q_idx, q in enumerate(parse_result.questions, start=1):
            ans_val = q.options[q.correct_option] if 0 <= q.correct_option < len(q.options) else None
            ext_q = ExtractedQuestion(
                question_text=q.display_question,
                options=q.options,
                answer_raw=ans_val,
                explanation=q.explanation,
                question_number=q_idx,
            )
            extracted_questions.append(ext_q)

        formatted_blocks = [q.to_formatted_block(default_number=i) for i, q in enumerate(extracted_questions, start=1)]
        raw_text = "\n\n".join(formatted_blocks)

        return ExtractionResult(
            questions=extracted_questions,
            raw_text=raw_text,
            total_found=len(extracted_questions),
            source_filename=filename,
        )

    def extract_from_file(self, file_path: str) -> ExtractionResult:
        """Read and parse from local file path."""
        with open(file_path, "rb") as f:
            content = f.read()
        return self.extract_from_bytes(content, filename=file_path)
