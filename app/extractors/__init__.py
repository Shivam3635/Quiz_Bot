"""Document extractors package."""

from app.extractors.base import BaseExtractor, ExtractedQuestion, ExtractionResult
from app.extractors.sheet_extractor import SheetExtractor, generate_quiz_template_bytes
from app.extractors.docx_extractor import DocxExtractor
from app.extractors.pdf_extractor import PdfExtractor
from app.extractors.vision_extractor import VisionExtractor
from app.extractors.router import DocumentRouter, IngestionSummary

__all__ = [
    "BaseExtractor",
    "ExtractedQuestion",
    "ExtractionResult",
    "SheetExtractor",
    "DocxExtractor",
    "PdfExtractor",
    "VisionExtractor",
    "DocumentRouter",
    "IngestionSummary",
    "generate_quiz_template_bytes",
]
