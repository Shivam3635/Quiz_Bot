"""Unit tests for Phase 1 spreadsheet (Excel/CSV) question extractor."""

import io
import pytest
import openpyxl

from app.extractors.sheet_extractor import SheetExtractor, generate_quiz_template_bytes
from app.parser.parser import BulkQuizParser


def test_csv_extraction_with_standard_headers():
    csv_content = """Question,Option A,Option B,Option C,Option D,Answer,Explanation
What is the capital of France?,Paris,London,Berlin,Madrid,A,Paris is the capital of France.
Which planet is known as the Red Planet?,Venus,Mars,Jupiter,Saturn,B,Mars appears red due to iron oxide.
"""
    file_bytes = csv_content.encode("utf-8")
    extractor = SheetExtractor()
    result = extractor.extract(file_bytes, "questions.csv")

    assert result.success is True
    assert result.error is None
    assert len(result.questions) == 2

    q1 = result.questions[0]
    assert "Paris" in q1.options
    assert q1.correct_index == 0
    assert q1.explanation == "Paris is the capital of France."

    # Verify formatting can be parsed cleanly by BulkQuizParser
    block = result.to_formatted_text()
    parse_res = BulkQuizParser().parse(block)
    assert not parse_res.has_errors
    assert parse_res.valid_count == 2
    assert parse_res.questions[0].correct_option == 0
    assert parse_res.questions[1].correct_option == 1


def test_csv_extraction_bilingual_hindi_english():
    csv_content = """Question,Hindi Question,Option A,Option B,Option C,Option D,Answer
What is the motto of NCC?,NCC का आदर्श वाक्य क्या है?,Unity and Duty,Unity and Discipline,Discipline and Service,Unity and Bravery,B
"""
    file_bytes = csv_content.encode("utf-8-sig")  # Test with UTF-8 BOM
    extractor = SheetExtractor()
    result = extractor.extract(file_bytes, "ncc_quiz.csv")

    assert result.success is True
    assert len(result.questions) == 1
    q = result.questions[0]
    assert "What is the motto of NCC?" in q.question_text
    assert "NCC का आदर्श वाक्य क्या है?" in q.question_text
    assert q.correct_index == 1

    formatted_block = q.to_formatted_block()
    assert "✅" in formatted_block
    assert "Unity and Discipline" in formatted_block


def test_excel_xlsx_extraction():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Quiz Questions"

    ws.append(["Question", "A", "B", "C", "D", "Correct Answer", "Explanation"])
    ws.append(["What is 2 + 2?", "3", "4", "5", "6", "B", "2+2=4"])
    ws.append(["What is the speed of light?", "3x10^8 m/s", "150 m/s", "1000 km/h", "Sound speed", "A", ""])

    out = io.BytesIO()
    wb.save(out)
    excel_bytes = out.getvalue()

    extractor = SheetExtractor()
    result = extractor.extract(excel_bytes, "math.xlsx")

    assert result.success is True
    assert len(result.questions) == 2
    assert result.questions[0].correct_index == 1
    assert result.questions[0].explanation == "2+2=4"
    assert result.questions[1].correct_index == 0


def test_generate_quiz_template_bytes():
    template_data = generate_quiz_template_bytes()
    assert len(template_data) > 0

    # Ensure it is a valid Excel workbook that openpyxl can load
    wb = openpyxl.load_workbook(io.BytesIO(template_data), data_only=True)
    assert "Quiz Questions" in wb.sheetnames
    ws = wb["Quiz Questions"]
    assert ws.cell(row=1, column=1).value == "Question"

    # Test extracting directly from the generated template!
    extractor = SheetExtractor()
    result = extractor.extract(template_data, "sample_template.xlsx")
    assert result.success is True
    assert len(result.questions) >= 3  # The 3 sample rows in the template


def test_single_column_raw_text_excel():
    wb = openpyxl.Workbook()
    ws = wb.active

    # Single column with complete question text blocks
    q_block = """Q1. What is Python?
A. A snake
B. A programming language ✅
C. A car
D. An island"""
    ws.append([q_block])

    out = io.BytesIO()
    wb.save(out)
    excel_bytes = out.getvalue()

    extractor = SheetExtractor()
    result = extractor.extract(excel_bytes, "raw_text.xlsx")

    assert result.success is True
    assert "What is Python?" in result.to_formatted_text()
    parse_res = BulkQuizParser().parse(result.to_formatted_text())
    assert not parse_res.has_errors
    assert parse_res.valid_count == 1
    assert parse_res.questions[0].correct_option == 1


def test_unsupported_file_extension():
    extractor = SheetExtractor()
    result = extractor.extract(b"dummy data", "document.pdf")
    assert result.success is False
    assert "Unsupported spreadsheet format" in result.error
