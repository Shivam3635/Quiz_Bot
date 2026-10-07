"""Unit tests for Phase 4 (DocumentRouter) and Phase 5 (VisionExtractor)."""

import io
import zipfile
import pytest
from unittest.mock import MagicMock, patch

from app.extractors.router import DocumentRouter, IngestionSummary
from app.extractors.vision_extractor import VisionExtractor


def test_router_detect_file_types():
    router = DocumentRouter()

    assert router.detect_file_type("quiz.xlsx", b"") == "excel"
    assert router.detect_file_type("quiz.csv", b"") == "csv"
    assert router.detect_file_type("quiz.docx", b"") == "word"
    assert router.detect_file_type("quiz.pdf", b"") == "pdf"
    assert router.detect_file_type("photo.jpg", b"") == "image"
    assert router.detect_file_type("photo.png", b"") == "image"
    assert router.detect_file_type("questions.txt", b"") == "text"
    assert router.detect_file_type("quizzes.zip", b"") == "zip"
    assert router.detect_file_type("doc.unknown", b"%PDF-1.4") == "pdf"


def test_router_file_size_limit():
    router = DocumentRouter()
    huge_bytes = b"0" * (21 * 1024 * 1024)
    summary = router.ingest(huge_bytes, "large_file.pdf")

    assert summary.success is False
    assert "exceeds 20MB limit" in summary.error_message


def test_router_empty_file():
    router = DocumentRouter()
    summary = router.ingest(b"", "empty.pdf")

    assert summary.success is False
    assert "empty (0 bytes)" in summary.error_message


def test_router_plain_text_ingest():
    router = DocumentRouter()
    txt = (
        "Q1. What is Python?\n"
        "A. Snake\n"
        "B. Language ✅\n\n"
        "Q2. What is 5 x 5?\n"
        "A. 25 ✅\n"
        "B. 30\n"
    )
    summary = router.ingest(txt.encode("utf-8"), "math.txt")

    assert summary.success is True
    assert summary.file_type == "text"
    assert summary.total_detected == 2
    assert "What is Python?" in summary.raw_text


def test_router_zip_archive_ingest():
    router = DocumentRouter()

    # Create in-memory zip archive with two question text files
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "part1.txt",
            "Q1. What is water?\nA. Liquid ✅\nB. Solid\n",
        )
        zf.writestr(
            "part2.txt",
            "Q2. What is ice?\nA. Liquid\nB. Solid ✅\n",
        )
    zip_bytes = buf.getvalue()

    summary = router.ingest(zip_bytes, "bundle.zip")

    assert summary.success is True
    assert summary.file_type == "zip"
    assert summary.total_detected == 2
    assert "Q1" in summary.raw_text
    assert "Q2" in summary.raw_text


def test_vision_extractor_no_api_key():
    extractor = VisionExtractor(api_key="")
    assert extractor.is_available() is False

    summary = extractor.extract(b"dummy_image_data", "exam.jpg")
    assert summary.success is False
    assert "AI Vision OCR is not configured" in summary.error


def test_vision_extractor_with_mocked_gemini():
    from PIL import Image

    # Create real 100x100 RGB image
    img = Image.new("RGB", (100, 100), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_bytes = buf.getvalue()

    mock_gemini_response = MagicMock()
    mock_gemini_response.text = (
        "Q1. What is the capital of France?\n"
        "A) London\n"
        "B) Paris ✅\n"
        "C) Berlin\n"
        "D) Rome\n"
        "Explanation: Paris is the capital of France.\n"
    )

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_gemini_response

    with patch("google.genai.Client", return_value=mock_client):
        extractor = VisionExtractor(api_key="mock_key_12345")
        result = extractor.extract(img_bytes, "test_exam.jpg")

        assert result.success is True
        assert len(result.questions) == 1
        q = result.questions[0]
        assert "capital of France" in q.question_text
        assert q.correct_index == 1
        assert q.explanation == "Paris is the capital of France."
