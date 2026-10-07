"""Unit tests for Phase 3 PDF (.pdf) question extractor."""

import io
import pytest
from unittest.mock import MagicMock, patch

from app.extractors.pdf_extractor import PdfExtractor
from app.parser.parser import BulkQuizParser


def _create_minimal_pdf_bytes(text_lines: list[str]) -> bytes:
    """Generate a valid minimal PDF in-memory with text stream lines."""
    stream_ops = ["BT", "/F1 12 Tf", "72 700 Td"]
    first = True
    for line in text_lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if first:
            stream_ops.append(f"({escaped}) Tj")
            first = False
        else:
            stream_ops.append("0 -20 Td")
            stream_ops.append(f"({escaped}) Tj")
    stream_ops.append("ET")
    stream_data = "\n".join(stream_ops).encode("latin-1")
    stream_len = len(stream_data)

    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"5 0 obj << /Length " + str(stream_len).encode("ascii") + b" >> stream\n"
        + stream_data + b"\nendstream\nendobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000227 00000 n \n"
        b"0000000298 00000 n \n"
        b"trailer << /Size 6 /Root 1 0 R >>\n"
        b"startxref\n500\n%%EOF"
    )
    return pdf


def test_pdf_extraction_basic():
    """Test extracting questions from a standard PDF."""
    pdf_bytes = _create_minimal_pdf_bytes([
        "Q1. What is the speed of light?",
        "A. 3x10^8 m/s",
        "B. 150 m/s",
        "C. 3000 km/h",
        "D. Sound speed",
        "Answer: A",
    ])

    extractor = PdfExtractor()
    result = extractor.extract(pdf_bytes, "physics.pdf")

    assert result.success is True
    assert result.error is None
    assert len(result.questions) == 1

    q = result.questions[0]
    assert "speed of light" in q.question_text
    assert q.correct_index == 0
    assert len(q.options) == 4

    # Verify through BulkQuizParser
    parse_res = BulkQuizParser().parse(result.to_formatted_text())
    assert not parse_res.has_errors
    assert parse_res.valid_count == 1
    assert parse_res.questions[0].correct_option == 0


def test_pdf_extraction_multi_question_with_footer():
    """Test PDF containing page numbering headers/footers and multiple questions."""
    page_text = (
        "Page 1 of 3\n"
        "Q1. What is Python?\n"
        "A. Snake\n"
        "B. Programming language\n"
        "Answer: B\n\n"
        "Q2. What is 2 + 2?\n"
        "A. 3\n"
        "B. 4\n"
        "Answer: B\n"
        "- 1 -\n"
    )

    mock_page = MagicMock()
    mock_page.extract_text.return_value = page_text

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        extractor = PdfExtractor()
        result = extractor.extract(b"%PDF-mock", "test.pdf")

        assert result.success is True
        assert len(result.questions) == 2
        assert result.questions[0].correct_index == 1
        assert result.questions[1].correct_index == 1

        parse_res = BulkQuizParser().parse(result.to_formatted_text())
        assert not parse_res.has_errors
        assert parse_res.valid_count == 2


def test_pdf_bilingual_questions():
    """Test bilingual Hindi/English questions extracted from PDF."""
    page_text = (
        "Q1. What is the motto of NCC? NCC का आदर्श वाक्य क्या है?\n"
        "A. Unity and Duty\n"
        "B. Unity and Discipline\n"
        "C. Discipline and Service\n"
        "D. Unity and Bravery\n"
        "Answer: B\n"
    )

    mock_page = MagicMock()
    mock_page.extract_text.return_value = page_text

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        extractor = PdfExtractor()
        result = extractor.extract(b"%PDF-mock", "ncc.pdf")

        assert result.success is True
        assert len(result.questions) == 1
        q = result.questions[0]
        assert "What is the motto of NCC?" in q.question_text
        assert "NCC का आदर्श वाक्य क्या है?" in q.question_text
        assert q.correct_index == 1


def test_pdf_empty_scanned_image_notice():
    """Test friendly message when a scanned PDF has no selectable text."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        extractor = PdfExtractor()
        result = extractor.extract(b"%PDF-mock", "scanned.pdf")

        assert result.success is False
        assert "No selectable text found in the PDF" in result.error


def test_pdf_password_protected():
    """Test handling of encrypted/password-protected PDF."""
    mock_reader = MagicMock()
    mock_reader.is_encrypted = True
    mock_reader.decrypt.side_effect = Exception("Password required")

    with patch("pypdf.PdfReader", return_value=mock_reader):
        extractor = PdfExtractor()
        result = extractor.extract(b"%PDF-mock", "protected.pdf")

        assert result.success is False
        assert "password protected" in result.error


def test_pdf_unsupported_extension():
    extractor = PdfExtractor()
    result = extractor.extract(b"dummy data", "document.docx")
    assert result.success is False
    assert "Unsupported PDF document format" in result.error


def test_pdf_bulleted_options_with_unicode_checkmark_and_preamble():
    """Test PDF matching NCC_Practice_Quiz format: title preamble, bulleted options, ✓ checkmarks."""
    page_text = (
        "NCC Practice Quiz 16\n"
        "25 MCQs • English + Hindi • Correct answers marked with ✓\n"
        "Q1. What is the effective range of a .22 Deluxe Rifle? .22 डीलक्स राइफल की कारगर रेंज क्या है?\n"
        "• A. 25 Yards (25 गज) ✓\n"
        "• B. 50 Yards (50 गज)\n"
        "• C. 300 Yards (300 गज)\n"
        "• D. 1700 Yards (1700 गज)\n"
        "Q2. What is the length of a .22 Deluxe Rifle? .22 डीलक्स राइफल की लंबाई क्या है?\n"
        "• A. 45 Inches (45 इंच)\n"
        "• B. 50 Inches (50 इंच)\n"
        "• C. 110 cm (110 सेमी)\n"
        "• D. 43 Inches (43 इंच) ✓\n"
    )
    mock_page = MagicMock()
    mock_page.extract_text.return_value = page_text

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        extractor = PdfExtractor()
        result = extractor.extract(b"%PDF-mock", "NCC_Practice_Quiz_16.pdf")

        assert result.success is True
        assert len(result.questions) == 2
        assert result.questions[0].correct_index == 0
        assert result.questions[1].correct_index == 3

        parse_res = BulkQuizParser().parse(result.to_formatted_text())
        assert not parse_res.has_errors
        assert parse_res.valid_count == 2
