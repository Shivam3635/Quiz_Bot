"""Unified Document Router and Ingestion Service (Phase 4 & 5)."""

import asyncio
import concurrent.futures
from dataclasses import dataclass, field
import io
import os
import re
import time
from typing import Optional
import zipfile
from PIL import Image
import pypdf

from app.config.settings import get_settings
from app.extractors.base import ExtractedQuestion, ExtractionResult
from app.extractors.sheet_extractor import SheetExtractor
from app.extractors.docx_extractor import DocxExtractor
from app.extractors.pdf_extractor import PAGE_ARTIFACT_RE, PDF_PROMO_NOISE_RE, PdfExtractor
from app.extractors.vision_extractor import VisionExtractor
from app.parser.parser import BulkQuizParser
from app.utils.devanagari_cleaner import clean_devanagari_text
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
        self.settings = get_settings()
        self.sheet_extractor = SheetExtractor()
        self.docx_extractor = DocxExtractor()
        self.pdf_extractor = PdfExtractor()
        self.vision_extractor = VisionExtractor()
        self.text_parser = BulkQuizParser()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self.max_ocr_pages = getattr(self.settings, "MAX_OCR_PAGES", 10)
        self.max_concurrent_extractions = getattr(self.settings, "MAX_CONCURRENT_EXTRACTIONS", 5)

    @property
    def executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """Lazily initialize the dedicated bounded ThreadPoolExecutor."""
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.max_concurrent_extractions,
                thread_name_prefix="doc_extractor",
            )
        return self._executor

    @property
    def extraction_semaphore(self) -> asyncio.Semaphore:
        """Lazily initialize the bounded extraction semaphore on the active event loop."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent_extractions)
        return self._semaphore

    def shutdown(self, wait: bool = False) -> None:
        """Shutdown the dedicated extraction executor without blocking event loop."""
        if self._executor is not None:
            self._executor.shutdown(wait=wait, cancel_futures=True)
            self._executor = None

    async def ingest_async(self, content: bytes, filename: str) -> IngestionSummary:
        """Asynchronously validate, route, and ingest document bytes outside the event loop with cancellation safety."""
        # 1. Quick size and empty validation (non-blocking)
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

        # 2. Acquire bounded semaphore permit
        await self.extraction_semaphore.acquire()
        permit_acquired = True

        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self.executor, self.ingest, content, filename)

            # Ownership of permit release is strictly tied to future completion in the thread pool
            future.add_done_callback(
                lambda _: loop.call_soon_threadsafe(self.extraction_semaphore.release)
            )
            permit_acquired = False

            # Shield future so coroutine cancellation does not abort the future or release semaphore early
            return await asyncio.shield(future)
        finally:
            # If an error occurred before callback registration, release permit immediately
            if permit_acquired:
                self.extraction_semaphore.release()

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
            # Evaluate whether the PDF appears scanned, hybrid, or its text layer is insufficient
            is_scanned_or_hybrid = False
            if not result.success:
                err_msg = result.error_message or ""
                if "No selectable text found" in err_msg:
                    is_scanned_or_hybrid = True
                elif "Could not detect questions in the PDF" in err_msg:
                    is_scanned_or_hybrid = self._has_scanned_pdf_characteristics(content)
            else:
                # Even if some questions were found, check if document has unextracted image pages (hybrid PDF)
                is_scanned_or_hybrid = self._has_unextracted_scanned_pages(content)

            if is_scanned_or_hybrid:
                if not self.vision_extractor.is_available():
                    logger.warning(
                        "Scanned/hybrid PDF '%s' detected but Gemini Vision is not configured",
                        filename,
                    )
                    if not (result.success and result.questions):
                        result = ExtractionResult(
                            error_message=(
                                "No selectable text found in the PDF. "
                                "This appears to be a scanned document, but AI Vision OCR is not configured "
                                "(GEMINI_API_KEY is missing). Please provide a searchable PDF or configure the API key."
                            ),
                            source_filename=filename,
                        )
                else:
                    logger.info("Attempting AI Vision OCR on scanned/hybrid PDF '%s'", filename)
                    ocr_res = self._extract_scanned_pdf_ocr(content, filename)
                    if ocr_res.success or (not result.success):
                        result = ocr_res
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

    def _has_scanned_pdf_characteristics(self, content: bytes) -> bool:
        """Check if PDF has characteristics of a scanned document (little text, but has images)."""
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                return False
            total_chars = 0
            has_images = False
            for page in reader.pages[:5]:
                txt = (page.extract_text() or "").strip()
                total_chars += len(txt)
                if len(page.images) > 0:
                    has_images = True
            return has_images and (total_chars < 100)
        except Exception:
            return False

    def _has_unextracted_scanned_pages(self, content: bytes) -> bool:
        """Check if any page in the PDF has embedded images and no selectable text (hybrid document)."""
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                return False
            for page in reader.pages[:self.max_ocr_pages]:
                txt = (page.extract_text() or "").strip()
                if len(txt) < 40 and len(page.images) > 0:
                    return True
            return False
        except Exception:
            return False

    def _extract_scanned_pdf_ocr(self, content: bytes, filename: str) -> ExtractionResult:
        """Extract quiz questions from a scanned or hybrid PDF, preserving text, layout, and rotation."""
        start_time = time.monotonic()
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
        except Exception as e:
            logger.exception("Failed to open PDF %s for OCR: %s", filename, e)
            return ExtractionResult(
                error_message=f"Failed to read PDF file '{filename}': {str(e)}",
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
                error_message=f"The PDF file '{filename}' contains 0 pages.",
                source_filename=filename,
            )

        if total_pages > self.max_ocr_pages:
            return ExtractionResult(
                error_message=(
                    f"The scanned PDF '{filename}' contains {total_pages} pages, which exceeds the maximum "
                    f"limit of {self.max_ocr_pages} pages for AI OCR processing. Please upload a smaller document."
                ),
                source_filename=filename,
            )

        page_results: dict[int, ExtractionResult] = {}
        pages_with_ocr = 0

        for p_idx, page in enumerate(reader.pages, start=1):
            # 1. Check selectable text on this page
            raw_page_text = ""
            try:
                raw_page_text = page.extract_text() or ""
            except Exception as txt_err:
                logger.warning("Error reading text on page %d of %s: %s", p_idx, filename, txt_err)

            cleaned_page_text = clean_devanagari_text(raw_page_text)
            text_lines = [
                line.strip()
                for line in cleaned_page_text.splitlines()
                if line.strip() and not PAGE_ARTIFACT_RE.match(line.strip()) and not PDF_PROMO_NOISE_RE.search(line.strip())
            ]
            meaningful_text = "\n".join(text_lines).strip()
            has_meaningful_text = len(meaningful_text) >= 40

            # 2. Check embedded images on this page
            page_images = []
            try:
                page_images = list(page.images)
            except Exception as img_list_err:
                logger.warning("Error listing images on page %d of %s: %s", p_idx, filename, img_list_err)

            has_images = len(page_images) > 0

            # SEC-5431-04: If page has meaningful text and NO images, preserve selectable text without OCR
            if has_meaningful_text and not has_images:
                parse_res = self.text_parser.parse(meaningful_text)
                extracted_qs = []
                for q in parse_res.questions:
                    ans_val = q.options[q.correct_option] if 0 <= q.correct_option < len(q.options) else None
                    extracted_qs.append(
                        ExtractedQuestion(
                            question_text=q.display_question,
                            options=q.options,
                            answer_raw=ans_val,
                            explanation=q.explanation,
                        )
                    )
                page_results[p_idx] = ExtractionResult(
                    questions=extracted_qs,
                    raw_text=meaningful_text,
                    total_found=len(extracted_qs),
                    source_filename=filename,
                )
                continue

            # If page has neither meaningful text nor images, skip
            if not has_meaningful_text and not has_images:
                continue

            # SEC-5431-02: Multi-image layout handling
            img: Optional[Image.Image] = None
            if len(page_images) == 1:
                try:
                    img = page_images[0].image
                except Exception as read_err:
                    logger.warning("Failed to decode image on page %d of %s: %s", p_idx, filename, read_err)
            elif len(page_images) > 1:
                try:
                    pil_imgs = [im_obj.image for im_obj in page_images]
                    total_area = sum(im.width * im.height for im in pil_imgs)
                    max_im = max(pil_imgs, key=lambda im: im.width * im.height)
                    max_area = max_im.width * max_im.height
                    if total_area > 0 and (max_area / total_area) >= 0.80:
                        img = max_im
                    else:
                        return ExtractionResult(
                            error_message=(
                                f"Page {p_idx} of '{filename}' contains multiple separate images with a complex layout "
                                "that cannot be reliably reconstructed without losing document structure. "
                                "Please upload single-image page scans or searchable text."
                            ),
                            source_filename=filename,
                        )
                except Exception as multi_err:
                    logger.warning("Failed to inspect multiple images on page %d of %s: %s", p_idx, filename, multi_err)

            if img is None:
                if has_meaningful_text:
                    parse_res = self.text_parser.parse(meaningful_text)
                    extracted_qs = []
                    for q in parse_res.questions:
                        ans_val = q.options[q.correct_option] if 0 <= q.correct_option < len(q.options) else None
                        extracted_qs.append(
                            ExtractedQuestion(
                                question_text=q.display_question,
                                options=q.options,
                                answer_raw=ans_val,
                                explanation=q.explanation,
                            )
                        )
                    page_results[p_idx] = ExtractionResult(
                        questions=extracted_qs,
                        raw_text=meaningful_text,
                        total_found=len(extracted_qs),
                        source_filename=filename,
                    )
                continue

            # SEC-5431-03: Page rotation handling
            rotation = int(page.get("/Rotate", 0) or 0)
            if rotation % 360 != 0:
                img = img.rotate(-rotation, expand=True)

            # Constrain dimensions to 2048
            if img.width > 2048 or img.height > 2048:
                img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            jpeg_bytes = buf.getvalue()

            # Execute OCR on this page
            pages_with_ocr += 1
            page_res = self.vision_extractor.extract_from_bytes(
                jpeg_bytes,
                filename=f"{filename}_page_{p_idx}.jpg",
            )

            # Check for fatal quota or authentication errors
            if page_res.error_message:
                err_lower = page_res.error_message.lower()
                if any(k in err_lower for k in ("quota", "rate limit", "429", "authentication failed")):
                    return ExtractionResult(
                        error_message=page_res.error_message,
                        source_filename=filename,
                    )

            # SEC-5431-04: If page also had meaningful text alongside image, merge without duplication
            if has_meaningful_text and page_res.success and page_res.raw_text:
                combined_page_text = meaningful_text + "\n\n" + page_res.raw_text
                merged_parse = self.text_parser.parse(combined_page_text)
                merged_qs = []
                for q in merged_parse.questions:
                    ans_val = q.options[q.correct_option] if 0 <= q.correct_option < len(q.options) else None
                    merged_qs.append(
                        ExtractedQuestion(
                            question_text=q.display_question,
                            options=q.options,
                            answer_raw=ans_val,
                            explanation=q.explanation,
                        )
                    )
                page_results[p_idx] = ExtractionResult(
                    questions=merged_qs,
                    raw_text=combined_page_text,
                    total_found=len(merged_qs),
                    source_filename=filename,
                )
            else:
                page_results[p_idx] = page_res

        # Assemble all pages strictly by ascending page index (1..N)
        all_questions: list[ExtractedQuestion] = []
        for p_idx in sorted(page_results.keys()):
            pres = page_results[p_idx]
            if pres.success and pres.questions:
                all_questions.extend(pres.questions)

        duration = time.monotonic() - start_time
        logger.info(
            "PDF extraction (hybrid/scanned) completed: file=%s, pages_total=%d, pages_ocr=%d, questions_found=%d, duration=%.2fs",
            filename,
            total_pages,
            pages_with_ocr,
            len(all_questions),
            duration,
        )

        if not all_questions:
            first_err = next((r.error_message for r in page_results.values() if r.error_message), None)
            return ExtractionResult(
                error_message=first_err or f"No quiz questions could be identified in PDF '{filename}'.",
                source_filename=filename,
            )

        reindexed_blocks = [
            q.to_formatted_block(default_number=i)
            for i, q in enumerate(all_questions, start=1)
        ]
        return ExtractionResult(
            questions=all_questions,
            raw_text="\n\n".join(reindexed_blocks),
            total_found=len(all_questions),
            source_filename=filename,
        )
