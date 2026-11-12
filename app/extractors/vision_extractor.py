"""Multimodal AI Vision & OCR extractor for images and scanned documents."""

import io
import os
import re
import time
from typing import Optional
from PIL import Image

from app.config.settings import get_settings
from app.extractors.base import BaseExtractor, ExtractedQuestion, ExtractionResult
from app.parser.parser import BulkQuizParser
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

VISION_PROMPT = """You are an expert quiz extractor. Analyze the provided image or scanned document page containing multiple-choice quiz questions.
Extract ALL questions, options, and answers following these strict rules:

1. Format each question sequentially as:
Q1. Question text? (If bilingual with Hindi, keep the English question followed by the Hindi translation with a question mark '?')
A) Option 1
B) Option 2
C) Option 3
D) Option 4
Answer: B (or mark the option with ✅ if indicated by a tick, circle, or answer key)
Explanation: (if an explanation or solution is provided)

2. If the document is in Hindi or bilingual (English + Hindi), preserve Devanagari text accurately.
3. If an answer is indicated by a tick mark, circle, underline, or answer key, specify it clearly in the 'Answer:' line or with ✅.
4. If options are not labeled A/B/C/D, assign standard letters A, B, C, D in order.
5. Do NOT include markdown code fences (no ```). Output plain text formatted as shown above.
"""


class VisionExtractor(BaseExtractor):
    """Extracts quiz questions from images and scanned documents using Gemini Vision."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.settings = get_settings()
        self.api_key = api_key or self.settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
        self.model_name = getattr(self.settings, "GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout_seconds = getattr(self.settings, "GEMINI_TIMEOUT_SECONDS", 30.0)
        self.parser = BulkQuizParser()

    def is_available(self) -> bool:
        """Check if Gemini Vision OCR is configured with an API key."""
        return bool(self.api_key)

    def extract_from_bytes(self, content: bytes, filename: str) -> ExtractionResult:
        """Extract quiz questions from raw image bytes (JPEG, PNG, WEBP, etc.)."""
        start_time = time.monotonic()
        if not self.is_available():
            return ExtractionResult(
                error_message=(
                    "AI Vision OCR is not configured. Please set GEMINI_API_KEY in your environment or .env file "
                    "to extract questions from photos, screenshots, and scanned images."
                ),
                source_filename=filename,
            )

        try:
            # Validate image can be opened with Pillow
            img = Image.open(io.BytesIO(content))
        except Exception as e:
            return ExtractionResult(
                error_message=f"Failed to read image '{filename}': {str(e)}",
                source_filename=filename,
            )

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)

            # Resize if dimensions exceed 2048 to keep payload size bounded
            if img.width > 2048 or img.height > 2048:
                img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

            # Convert image to RGB if necessary for JPEG/PNG
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            jpeg_bytes = buf.getvalue()

            config = types.GenerateContentConfig(
                http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
            )

            response = client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                    VISION_PROMPT,
                ],
                config=config,
            )

            response_text = response.text or ""
        except Exception as exc:
            err_str = str(exc)
            if self.api_key:
                err_str = err_str.replace(self.api_key, "[REDACTED_API_KEY]")
            err_str = re.sub(r'AIza[0-9A-Za-z-_]{35}', '[REDACTED_API_KEY]', err_str)
            logger.exception("Gemini Vision OCR error for %s: %s", filename, err_str)

            err_lower = err_str.lower()
            if any(k in err_lower for k in ("429", "resource_exhausted", "quota", "rate limit")):
                msg = "Gemini API rate limit or quota exceeded. Please wait a moment or try again later."
            elif any(k in err_lower for k in ("401", "403", "unauthenticated", "permission_denied", "api_key_invalid")):
                msg = "Gemini API authentication failed. Please verify your GEMINI_API_KEY configuration."
            elif any(k in err_lower for k in ("timeout", "timed out", "timeoutexception")):
                msg = "AI Vision processing timed out. Please try again with a smaller or lower-resolution image."
            else:
                msg = f"AI Vision processing failed: {err_str}"

            return ExtractionResult(
                error_message=msg,
                source_filename=filename,
            )

        if not response_text.strip():
            return ExtractionResult(
                error_message=f"No quiz questions could be identified in the image '{filename}'.",
                source_filename=filename,
            )

        # Parse text response with BulkQuizParser
        parse_result = self.parser.parse(response_text)
        if not parse_result.questions:
            return ExtractionResult(
                raw_text=response_text,
                total_found=0,
                error_message="Could not parse structured questions from the image text.",
                source_filename=filename,
            )

        extracted_questions: list[ExtractedQuestion] = []
        for idx, q in enumerate(parse_result.questions, start=1):
            ans_val = q.options[q.correct_option] if 0 <= q.correct_option < len(q.options) else None
            ext_q = ExtractedQuestion(
                question_text=q.display_question,
                options=q.options,
                answer_raw=ans_val,
                explanation=q.explanation,
                question_number=idx,
            )
            extracted_questions.append(ext_q)

        formatted_blocks = [q.to_formatted_block(default_number=i) for i, q in enumerate(extracted_questions, start=1)]
        raw_text = "\n\n".join(formatted_blocks)
        duration = time.monotonic() - start_time
        logger.info(
            "AI Vision extraction completed: file=%s, duration=%.2fs, questions_found=%d",
            filename,
            duration,
            len(extracted_questions),
        )

        return ExtractionResult(
            questions=extracted_questions,
            raw_text=raw_text,
            total_found=len(extracted_questions),
            source_filename=filename,
        )

    def extract_from_file(self, file_path: str) -> ExtractionResult:
        """Read and parse from local image file path."""
        with open(file_path, "rb") as f:
            content = f.read()
        return self.extract_from_bytes(content, filename=file_path)

    async def extract_async(self, content: bytes, filename: str) -> ExtractionResult:
        """Asynchronously extract questions from image bytes outside the event loop."""
        import asyncio
        return await asyncio.to_thread(self.extract_from_bytes, content, filename)

    async def extract_from_bytes_async(self, content: bytes, filename: str) -> ExtractionResult:
        """Alias for extract_async."""
        return await self.extract_async(content, filename)
