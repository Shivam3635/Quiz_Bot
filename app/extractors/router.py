"""Unified Document Router and Ingestion Service (Phase 4 & 5)."""

from dataclasses import dataclass, field
import io
import os
import re
from typing import Optional
import zipfile

from app.extractors.base import ExtractedQuestion, ExtractionResult
from app.extractors.sheet_extractor import SheetExtractor
from app.extractors.docx_extractor import DocxExtractor
from app.extractors.pdf_extractor import PdfExtractor
from app.extractors.vision_extractor import VisionExtractor
from app.parser.parser import BulkQuizParser
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB Telegram limit
MAX_ZIP_ENTRIES = 50
MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50MB
MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES = 20 * 1024 * 1024  # 20MB
MAX_COMPRESSION_RATIO = 100  # Zip-bomb protection: max 100:1 ratio for entries > 1MB


def is_safe_zip_member(zinfo: zipfile.ZipInfo) -> tuple[bool, Optional[str]]:
    """Validate archive member for path traversal, symlinks, and unsafe attributes."""
    name = zinfo.filename

    # Check for path traversal attempts
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[a-zA-Z]:", name):
        return False, f"Absolute path rejected: '{name}'"

    parts = normalized.split("/")
    if ".." in parts:
        return False, f"Path traversal rejected: '{name}'"

    # Reject Unix symbolic links (S_IFLNK = 0o120000)
    is_symlink = (zinfo.external_attr >> 16) & 0o170000 == 0o120000
    if is_symlink:
        return False, f"Symbolic link rejected: '{name}'"

    return True, None


@dataclass
class IngestionSummary:
    """Summary of processed document ingestion."""
    filename: str
    file_type: str
    total_detected: int = 0
    questions: list[ExtractedQuestion] = field(default_factory=list)
    raw_text: str = ""
    preview_snippet: str = ""
    success: bool = False
    error_message: Optional[str] = None


class DocumentRouter:
    """Routes incoming documents and media to specialized extractors with validation."""

    def __init__(self) -> None:
        self.sheet_extractor = SheetExtractor()
        self.docx_extractor = DocxExtractor()
        self.pdf_extractor = PdfExtractor()
        self.vision_extractor = VisionExtractor()
        self.text_parser = BulkQuizParser()

    def detect_file_type(self, filename: str, content: bytes) -> str:
        """Identify document category based on extension and magic bytes."""
        lower_name = filename.lower()

        # Check by extension first
        if lower_name.endswith((".xlsx", ".xlsm", ".xltx")):
            return "excel"
        if lower_name.endswith(".csv"):
            return "csv"
        if lower_name.endswith(".docx"):
            return "word"
        if lower_name.endswith(".pdf"):
            return "pdf"
        if lower_name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return "image"
        if lower_name.endswith(".txt"):
            return "text"
        if lower_name.endswith(".zip"):
            return "zip"

        # Check by magic header bytes
        if content.startswith(b"%PDF-"):
            return "pdf"
        if content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff"):
            return "image"
        if content.startswith(b"PK\x03\x04"):
            # Could be docx, xlsx, or zip
            return "zip"

        return "unknown"

    def ingest(self, content: bytes, filename: str) -> IngestionSummary:
        """Validate, route, and ingest document bytes into structured questions."""
        # 1. Size validation
        if len(content) > MAX_FILE_BYTES:
            return IngestionSummary(
                filename=filename,
                file_type="unknown",
                error_message=f"File exceeds 20MB limit ({len(content) // (1024 * 1024)}MB). Please upload a smaller file.",
            )

        if len(content) == 0:
            return IngestionSummary(
                filename=filename,
                file_type="unknown",
                error_message="The uploaded file is empty (0 bytes).",
            )

        # 2. Detect type
        file_type = self.detect_file_type(filename, content)

        # 3. Route to extractor
        if file_type in ("excel", "csv"):
            result = self.sheet_extractor.extract_from_bytes(content, filename)
        elif file_type == "word":
            result = self.docx_extractor.extract_from_bytes(content, filename)
        elif file_type == "pdf":
            result = self.pdf_extractor.extract_from_bytes(content, filename)
            # If standard text extraction found no selectable text (scanned PDF) and vision is available
            if not result.success and "No selectable text found" in (result.error_message or ""):
                if self.vision_extractor.is_available():
                    logger.info("Attempting AI Vision OCR on scanned PDF %s", filename)
                    # Convert first page of PDF or delegate
                    # Note: For pure image extraction, VisionExtractor handles images
                    pass
        elif file_type == "image":
            result = self.vision_extractor.extract_from_bytes(content, filename)
        elif file_type == "text":
            result = self._ingest_text(content, filename)
        elif file_type == "zip":
            result = self._ingest_zip(content, filename)
        else:
            return IngestionSummary(
                filename=filename,
                file_type="unsupported",
                error_message=(
                    f"Unsupported file format '{filename}'. Supported formats: "
                    "Excel (.xlsx), CSV (.csv), Word (.docx), PDF (.pdf), Images (.png, .jpg), and ZIP (.zip)."
                ),
            )

        if not result.success or not result.questions:
            return IngestionSummary(
                filename=filename,
                file_type=file_type,
                error_message=result.error_message or "No valid quiz questions could be detected.",
            )

        # Build preview snippet
        preview_lines = []
        if result.questions:
            first_q = result.questions[0]
            preview_lines.append(f"Q1: {first_q.question_text[:60]}...")
            if first_q.options:
                preview_lines.append(f"Options ({len(first_q.options)}): {', '.join(first_q.options[:2])}...")
        preview_snippet = "\n".join(preview_lines)

        return IngestionSummary(
            filename=filename,
            file_type=file_type,
            total_detected=len(result.questions),
            questions=result.questions,
            raw_text=result.raw_text,
            preview_snippet=preview_snippet,
            success=True,
        )

    def _ingest_text(self, content: bytes, filename: str) -> ExtractionResult:
        """Parse plain text .txt files."""
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return ExtractionResult(
                error_message="Could not decode text file. Ensure it is saved in UTF-8 format.",
                source_filename=filename,
            )

        parse_result = self.text_parser.parse(text)
        if not parse_result.questions:
            return ExtractionResult(
                error_message="No valid quiz questions found in the text file.",
                source_filename=filename,
            )

        extracted: list[ExtractedQuestion] = []
        for idx, q in enumerate(parse_result.questions, start=1):
            ans_val = q.options[q.correct_option] if 0 <= q.correct_option < len(q.options) else None
            ext = ExtractedQuestion(
                question_text=q.display_question,
                options=q.options,
                answer_raw=ans_val,
                explanation=q.explanation,
                question_number=idx,
            )
            extracted.append(ext)

        blocks = [q.to_formatted_block(default_number=i) for i, q in enumerate(extracted, start=1)]
        return ExtractionResult(
            questions=extracted,
            raw_text="\n\n".join(blocks),
            total_found=len(extracted),
            source_filename=filename,
        )

    def _ingest_zip(self, content: bytes, filename: str) -> ExtractionResult:
        """Unpack ZIP archive with strict security bounds against zip-bombs and path traversal."""
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except Exception as e:
            return ExtractionResult(
                error_message=f"Corrupt or invalid ZIP archive: {str(e)}",
                source_filename=filename,
            )

        infolist = zf.infolist()

        # 1. Archive entry count limit
        if len(infolist) > MAX_ZIP_ENTRIES:
            return ExtractionResult(
                error_message=(
                    f"ZIP archive contains {len(infolist)} entries, exceeding the maximum allowed "
                    f"limit of {MAX_ZIP_ENTRIES} files."
                ),
                source_filename=filename,
            )

        # 2. Pre-extraction aggregate size and compression ratio checks
        total_declared_uncompressed = 0
        for zinfo in infolist:
            # Check individual file uncompressed size
            if zinfo.file_size > MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES:
                return ExtractionResult(
                    error_message=(
                        f"Archive entry '{zinfo.filename}' uncompressed size ({zinfo.file_size // (1024 * 1024)}MB) "
                        f"exceeds the maximum 20MB limit."
                    ),
                    source_filename=filename,
                )

            # Check compression ratio for zip bomb defense and zero compressed size
            if zinfo.compress_size == 0 and zinfo.file_size > 0:
                return ExtractionResult(
                    error_message=(
                        f"Archive entry '{zinfo.filename}' has zero compressed size with non-zero uncompressed size, "
                        "rejected for security reasons."
                    ),
                    source_filename=filename,
                )
            elif zinfo.compress_size > 0:
                ratio = zinfo.file_size / zinfo.compress_size
                if ratio > MAX_COMPRESSION_RATIO and zinfo.file_size > 1024 * 1024:
                    return ExtractionResult(
                        error_message=(
                            f"Archive entry '{zinfo.filename}' exhibits an excessive compression ratio ({ratio:.1f}:1), "
                            "rejected for security reasons."
                        ),
                        source_filename=filename,
                    )

            if not zinfo.is_dir():
                total_declared_uncompressed += zinfo.file_size

        if total_declared_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
            return ExtractionResult(
                error_message=(
                    f"Total uncompressed content ({total_declared_uncompressed // (1024 * 1024)}MB) "
                    f"exceeds the maximum allowed limit of {MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES // (1024 * 1024)}MB."
                ),
                source_filename=filename,
            )

        all_questions: list[ExtractedQuestion] = []
        raw_blocks: list[str] = []
        cumulative_extracted_bytes = 0

        for zinfo in infolist:
            member_name = zinfo.filename

            # Skip directories and hidden / macOS metadata files
            if zinfo.is_dir() or member_name.endswith("/") or member_name.startswith("__MACOSX/") or member_name.startswith("."):
                continue

            # Path traversal and symlink validation
            is_safe, reject_reason = is_safe_zip_member(zinfo)
            if not is_safe:
                logger.warning("Skipping unsafe ZIP entry in '%s': %s", filename, reject_reason)
                continue

            # Read member bytes safely using bounded streaming reads (64KB chunks)
            member_chunks: list[bytes] = []
            member_extracted_bytes = 0

            try:
                with zf.open(zinfo, "r") as member_file:
                    while True:
                        chunk = member_file.read(64 * 1024)
                        if not chunk:
                            break
                        member_extracted_bytes += len(chunk)

                        # Enforce single-member decompressed limit during streaming
                        if member_extracted_bytes > MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES:
                            return ExtractionResult(
                                error_message=(
                                    f"Archive entry '{member_name}' decompressed size exceeded "
                                    f"the maximum allowed limit of {MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES // (1024 * 1024)}MB."
                                ),
                                source_filename=filename,
                            )

                        # Enforce aggregate decompressed limit during streaming
                        if cumulative_extracted_bytes + member_extracted_bytes > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
                            return ExtractionResult(
                                error_message=(
                                    f"Total decompressed size exceeded safety limit of "
                                    f"{MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES // (1024 * 1024)}MB during extraction."
                                ),
                                source_filename=filename,
                            )

                        member_chunks.append(chunk)
            except Exception as read_err:
                logger.warning("Could not read ZIP entry '%s': %s", member_name, read_err)
                continue

            cumulative_extracted_bytes += member_extracted_bytes
            member_bytes = b"".join(member_chunks)

            sub_type = self.detect_file_type(member_name, member_bytes)
            # Never process nested ZIP archives recursively
            if sub_type in ("excel", "csv", "word", "pdf", "text", "image"):
                sub_summary = self.ingest(member_bytes, member_name)
                if sub_summary.success and sub_summary.questions:
                    all_questions.extend(sub_summary.questions)
                    raw_blocks.append(sub_summary.raw_text)

        if not all_questions:
            return ExtractionResult(
                error_message=f"No quiz questions found inside ZIP archive '{filename}'.",
                source_filename=filename,
            )

        # Re-index all combined questions sequentially
        reindexed_blocks = [q.to_formatted_block(default_number=i) for i, q in enumerate(all_questions, start=1)]
        return ExtractionResult(
            questions=all_questions,
            raw_text="\n\n".join(reindexed_blocks),
            total_found=len(all_questions),
            source_filename=filename,
        )
