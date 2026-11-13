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


# ==============================================================================
# Phase 5.4.3: Async Extraction & Scanned PDF OCR Regression Tests
# ==============================================================================

import asyncio
import time
from PIL import Image
import reportlab.pdfgen.canvas as canvas
from app.extractors.base import ExtractionResult, ExtractedQuestion


def _create_scanned_pdf_bytes(num_pages: int = 1) -> bytes:
    """Helper to generate in-memory scanned PDF with embedded raster images."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(num_pages):
        img = Image.new("RGB", (120, 120), color=("red" if i % 2 == 0 else "blue"))
        c.drawInlineImage(img, 50, 500, 120, 120)
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_ingest_async_non_blocking_event_loop():
    """Verify that ingest_async offloads blocking work to a thread without freezing the asyncio event loop."""
    router = DocumentRouter()

    ticks = 0
    done = False

    async def heartbeat():
        nonlocal ticks
        while not done:
            ticks += 1
            await asyncio.sleep(0.04)

    # Patch sheet extractor to simulate a CPU or I/O-blocking extraction
    def blocking_extract(content, filename):
        time.sleep(0.25)
        return ExtractionResult(
            questions=[ExtractedQuestion("Sample question?", ["A", "B"], answer_raw="A", question_number=1)],
            raw_text="Q1. Sample question?\nA) A ✅\nB) B",
            total_found=1,
            source_filename=filename,
        )

    with patch.object(router.sheet_extractor, "extract_from_bytes", side_effect=blocking_extract):
        hb_task = asyncio.create_task(heartbeat())
        try:
            summary = await router.ingest_async(b"fake_excel_bytes", "test.xlsx")
            assert summary.success is True
            assert summary.total_detected == 1
        finally:
            done = True
            await hb_task

    # If the event loop were blocked by time.sleep, ticks would be 0 or 1.
    # Heartbeat must have ticked multiple times while extraction ran in background thread.
    assert ticks >= 3, f"Expected event loop heartbeat to tick while extraction was running, but got {ticks} ticks."


@pytest.mark.asyncio
async def test_ingest_async_exception_propagation():
    """Verify exceptions in worker threads propagate cleanly when awaiting ingest_async."""
    router = DocumentRouter()

    def crashing_extract(content, filename):
        raise ValueError("Simulated extractor crash")

    with patch.object(router.sheet_extractor, "extract_from_bytes", side_effect=crashing_extract):
        with pytest.raises(ValueError, match="Simulated extractor crash"):
            await router.ingest_async(b"excel_bytes", "crash.xlsx")


import threading


@pytest.mark.asyncio
async def test_ingest_async_cancellation_preserves_semaphore_and_bounds_workers():
    """Verify that when a coroutine is cancelled, the semaphore permit remains held

    until the underlying worker thread actually finishes, preventing capacity bypass.
    """
    router = DocumentRouter()
    router.max_concurrent_extractions = 2
    # Ensure semaphore matches configured bound
    router._semaphore = asyncio.Semaphore(2)

    worker_started = threading.Event()
    worker_can_finish = threading.Event()
    worker_finished = threading.Event()

    def blocking_worker(content, filename):
        worker_started.set()
        worker_can_finish.wait(timeout=2.0)
        worker_finished.set()
        return ExtractionResult(total_found=0, source_filename=filename)

    with patch.object(router.sheet_extractor, "extract_from_bytes", side_effect=blocking_worker):
        # 1. Start task 1
        t1 = asyncio.create_task(router.ingest_async(b"doc1", "doc1.xlsx"))
        await asyncio.sleep(0.05)
        assert worker_started.wait(timeout=1.0)

        # Semaphore value should be 1 (1 permit held by t1's worker)
        assert router.extraction_semaphore._value == 1

        # 2. Cancel t1 while its worker is still running
        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1

        # 3. CRITICAL: The worker is still running, so semaphore value MUST remain 1 (NOT 2)!
        assert not worker_finished.is_set(), "Worker should still be running"
        assert router.extraction_semaphore._value == 1, (
            f"Semaphore permit leaked prematurely on cancellation! Expected 1, got {router.extraction_semaphore._value}"
        )

        # 4. Now release the worker and verify the permit is returned upon thread completion
        worker_can_finish.set()
        assert worker_finished.wait(timeout=1.0)
        # Yield to let call_soon_threadsafe callback run on event loop
        await asyncio.sleep(0.05)
        assert router.extraction_semaphore._value == 2, "Semaphore permit must be returned after worker completes"


@pytest.mark.asyncio
async def test_ingest_async_repeated_cancellation_bounds_active_workers():
    """Verify repeated rapid task cancellations never exceed MAX_CONCURRENT_EXTRACTIONS."""
    router = DocumentRouter()
    router.max_concurrent_extractions = 2
    router._semaphore = asyncio.Semaphore(2)

    active_workers = 0
    max_observed_workers = 0
    worker_lock = threading.Lock()
    can_finish = threading.Event()

    def tracked_worker(content, filename):
        nonlocal active_workers, max_observed_workers
        with worker_lock:
            active_workers += 1
            if active_workers > max_observed_workers:
                max_observed_workers = active_workers
        can_finish.wait(timeout=2.0)
        with worker_lock:
            active_workers -= 1
        return ExtractionResult(total_found=0, source_filename=filename)

    with patch.object(router.sheet_extractor, "extract_from_bytes", side_effect=tracked_worker):
        # Submit 5 tasks in succession and cancel each one
        tasks = []
        for i in range(5):
            t = asyncio.create_task(router.ingest_async(b"doc", f"doc{i}.xlsx"))
            tasks.append(t)
            await asyncio.sleep(0.02)
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        # At no point should more than 2 worker threads have executed concurrently
        assert max_observed_workers <= 2, f"Observed {max_observed_workers} concurrent workers (max is 2)!"
        can_finish.set()
        # Wait for threads to drain
        await asyncio.sleep(0.2)
        assert active_workers == 0


@pytest.mark.asyncio
async def test_extract_async_vision_and_base():
    """Verify BaseExtractor.extract_async and VisionExtractor.extract_async."""
    extractor = VisionExtractor(api_key="mock_key")
    img = Image.new("RGB", (50, 50), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")

    mock_resp = MagicMock()
    mock_resp.text = "Q1. What is AI?\nA) Tool ✅\nB) Animal\n"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch("google.genai.Client", return_value=mock_client):
        res = await extractor.extract_async(buf.getvalue(), "ai.jpg")
        assert res.success is True
        assert len(res.questions) == 1
        assert "What is AI?" in res.questions[0].question_text


def test_vision_extractor_timeout_handling():
    """Verify timeout exception produces a user-friendly classified error."""
    import httpx
    extractor = VisionExtractor(api_key="mock_key")
    img = Image.new("RGB", (50, 50), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = httpx.TimeoutException("Read timeout after 30s")

    with patch("google.genai.Client", return_value=mock_client):
        res = extractor.extract(buf.getvalue(), "photo.jpg")
        assert res.success is False
        assert "timed out" in res.error.lower()


def test_vision_extractor_rate_limit_handling():
    """Verify 429 rate limit or quota exceeded produces classified error."""
    extractor = VisionExtractor(api_key="mock_key")
    img = Image.new("RGB", (50, 50), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED: quota exceeded")

    with patch("google.genai.Client", return_value=mock_client):
        res = extractor.extract(buf.getvalue(), "photo.jpg")
        assert res.success is False
        assert "rate limit or quota" in res.error.lower()


def test_vision_extractor_auth_error_handling():
    """Verify 401 or invalid key produces classified error."""
    extractor = VisionExtractor(api_key="mock_key")
    img = Image.new("RGB", (50, 50), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("401 UNAUTHENTICATED: API_KEY_INVALID")

    with patch("google.genai.Client", return_value=mock_client):
        res = extractor.extract(buf.getvalue(), "photo.jpg")
        assert res.success is False
        assert "authentication failed" in res.error.lower()


def test_vision_extractor_redacts_api_key():
    """Verify API keys are never exposed in error messages."""
    secret_key = "AIzaSySecretApiKey1234567890abcdef"
    extractor = VisionExtractor(api_key=secret_key)
    img = Image.new("RGB", (50, 50), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception(f"Internal error with key {secret_key}")

    with patch("google.genai.Client", return_value=mock_client):
        res = extractor.extract(buf.getvalue(), "photo.jpg")
        assert res.success is False
        assert secret_key not in res.error
        assert "[REDACTED_API_KEY]" in res.error


def test_scanned_pdf_ocr_triggered_when_no_text():
    """Verify scanned PDF with no selectable text triggers OCR fallback."""
    pdf_bytes = _create_scanned_pdf_bytes(1)
    router = DocumentRouter()

    mock_ocr_result = ExtractionResult(
        questions=[
            ExtractedQuestion(
                question_text="What is the capital of India?",
                options=["New Delhi", "Mumbai", "Kolkata", "Chennai"],
                answer_raw="New Delhi",
                question_number=1,
            )
        ],
        raw_text="Q1. What is the capital of India?\nA) New Delhi ✅\nB) Mumbai\nC) Kolkata\nD) Chennai",
        total_found=1,
        source_filename="scanned.pdf",
    )

    with patch.object(router.vision_extractor, "is_available", return_value=True):
        with patch.object(router.vision_extractor, "extract_from_bytes", return_value=mock_ocr_result) as mock_ocr:
            summary = router.ingest(pdf_bytes, "scanned.pdf")
            assert summary.success is True
            assert summary.file_type == "pdf"
            assert summary.total_detected == 1
            assert mock_ocr.called
            assert "capital of India" in summary.raw_text


def test_scanned_pdf_multi_page_ordering():
    """Verify multi-page scanned PDF processes pages in ascending sequential order."""
    pdf_bytes = _create_scanned_pdf_bytes(3)
    router = DocumentRouter()

    call_order = []

    def mock_page_ocr(content, filename):
        call_order.append(filename)
        page_num = len(call_order)
        return ExtractionResult(
            questions=[
                ExtractedQuestion(
                    question_text=f"Question from page {page_num}?",
                    options=["Opt A", "Opt B"],
                    answer_raw="Opt A",
                    question_number=1,
                )
            ],
            raw_text=f"Q1. Question from page {page_num}?\nA) Opt A ✅\nB) Opt B",
            total_found=1,
            source_filename=filename,
        )

    with patch.object(router.vision_extractor, "is_available", return_value=True):
        with patch.object(router.vision_extractor, "extract_from_bytes", side_effect=mock_page_ocr):
            summary = router.ingest(pdf_bytes, "exam.pdf")
            assert summary.success is True
            assert summary.total_detected == 3
            # Check question texts are assembled in strict 1, 2, 3 order
            assert "Question from page 1" in summary.questions[0].question_text
            assert "Question from page 2" in summary.questions[1].question_text
            assert "Question from page 3" in summary.questions[2].question_text


def test_scanned_pdf_page_limit_enforced():
    """Verify scanned PDF exceeding MAX_OCR_PAGES returns clear actionable error."""
    pdf_bytes = _create_scanned_pdf_bytes(12)
    router = DocumentRouter()
    router.max_ocr_pages = 10

    with patch.object(router.vision_extractor, "is_available", return_value=True):
        summary = router.ingest(pdf_bytes, "huge_exam.pdf")
        assert summary.success is False
        assert "exceeds the maximum limit of 10 pages" in summary.error_message


def test_scanned_pdf_password_protected():
    """Verify password protected PDF is rejected safely without crashing."""
    router = DocumentRouter()
    mock_reader = MagicMock()
    mock_reader.is_encrypted = True
    mock_reader.decrypt.side_effect = Exception("Protected")

    with patch("pypdf.PdfReader", return_value=mock_reader):
        with patch.object(router.vision_extractor, "is_available", return_value=True):
            summary = router.ingest(b"%PDF-mock", "locked.pdf")
            assert summary.success is False
            assert "password protected" in summary.error_message


def test_scanned_pdf_empty_ocr_result():
    """Verify empty OCR result returns clear actionable error."""
    pdf_bytes = _create_scanned_pdf_bytes(1)
    router = DocumentRouter()

    empty_result = ExtractionResult(
        questions=[],
        raw_text="",
        total_found=0,
        error_message="No quiz questions could be identified in the image.",
        source_filename="scanned.pdf",
    )

    with patch.object(router.vision_extractor, "is_available", return_value=True):
        with patch.object(router.vision_extractor, "extract_from_bytes", return_value=empty_result):
            summary = router.ingest(pdf_bytes, "empty_scan.pdf")
            assert summary.success is False
            assert "No quiz questions could be identified" in summary.error_message


def test_scanned_pdf_without_api_key():
    """Verify scanned PDF uploaded without GEMINI_API_KEY configured returns clear actionable error."""
    pdf_bytes = _create_scanned_pdf_bytes(1)
    router = DocumentRouter()

    with patch.object(router.vision_extractor, "is_available", return_value=False):
        summary = router.ingest(pdf_bytes, "no_key.pdf")
        assert summary.success is False
        assert "AI Vision OCR is not configured" in summary.error_message


def test_text_pdf_does_not_trigger_ocr():
    """Verify that a normal text-based PDF extracts questions directly without OCR."""
    router = DocumentRouter()

    mock_text_result = ExtractionResult(
        questions=[
            ExtractedQuestion(
                question_text="What is gravity?",
                options=["Force", "Magic"],
                answer_raw="Force",
                question_number=1,
            )
        ],
        raw_text="Q1. What is gravity?\nA) Force ✅\nB) Magic",
        total_found=1,
        source_filename="physics.pdf",
    )

    with patch.object(router.pdf_extractor, "extract_from_bytes", return_value=mock_text_result):
        with patch.object(router.vision_extractor, "extract_from_bytes") as mock_vision:
            summary = router.ingest(b"%PDF-text", "physics.pdf")
            assert summary.success is True
            assert summary.total_detected == 1
            # VisionExtractor must NOT be called for standard text PDFs
            mock_vision.assert_not_called()


def test_scanned_pdf_multi_image_dominant_selection():
    """Verify that a page with a dominant full-page scan and a tiny logo/icon selects the dominant image."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    # Dominant image: 400x400
    dom_img = Image.new("RGB", (400, 400), color="blue")
    c.drawInlineImage(dom_img, 50, 200, 400, 400)
    # Tiny logo: 20x20
    tiny_img = Image.new("RGB", (20, 20), color="red")
    c.drawInlineImage(tiny_img, 50, 700, 20, 20)
    c.showPage()
    c.save()

    pdf_bytes = buf.getvalue()
    router = DocumentRouter()

    captured_sizes = []

    def mock_extract(jpeg_bytes, filename):
        im = Image.open(io.BytesIO(jpeg_bytes))
        captured_sizes.append(im.size)
        return ExtractionResult(
            questions=[ExtractedQuestion("Q from dominant?", ["A", "B"], answer_raw="A", question_number=1)],
            raw_text="Q1. Q from dominant?\nA) A ✅\nB) B",
            total_found=1,
            source_filename=filename,
        )

    with patch.object(router.vision_extractor, "is_available", return_value=True):
        with patch.object(router.vision_extractor, "extract_from_bytes", side_effect=mock_extract):
            summary = router.ingest(pdf_bytes, "dominant.pdf")
            assert summary.success is True
            assert summary.total_detected == 1
            # Captured size should be the dominant 400x400 image, NOT vertically stacked with 20x20
            assert len(captured_sizes) == 1
            assert captured_sizes[0] == (400, 400)


def test_scanned_pdf_multi_image_complex_layout_error():
    """Verify that a page with multiple separate significant images returns a clear error instead of scrambling layout."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    # Two equal-sized significant images (50/50 area)
    img1 = Image.new("RGB", (200, 200), color="blue")
    img2 = Image.new("RGB", (200, 200), color="green")
    c.drawInlineImage(img1, 50, 500, 200, 200)
    c.drawInlineImage(img2, 50, 200, 200, 200)
    c.showPage()
    c.save()

    pdf_bytes = buf.getvalue()
    router = DocumentRouter()

    with patch.object(router.vision_extractor, "is_available", return_value=True):
        summary = router.ingest(pdf_bytes, "split_page.pdf")
        assert summary.success is False
        assert "multiple separate images with a complex layout" in summary.error_message


def test_scanned_pdf_rotation_handling():
    """Verify PDF page rotations (0, 90, 180, 270) correctly orient the image before OCR."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)

    # Page 1: 0 deg rotation, 200 wide x 100 high
    im1 = Image.new("RGB", (200, 100), color="red")
    c.drawInlineImage(im1, 50, 500, 200, 100)
    c.showPage()

    # Page 2: 90 deg rotation, 200 wide x 100 high (should become 100 wide x 200 high)
    c.setPageRotation(90)
    im2 = Image.new("RGB", (200, 100), color="green")
    c.drawInlineImage(im2, 50, 500, 200, 100)
    c.showPage()

    c.save()

    pdf_bytes = buf.getvalue()
    router = DocumentRouter()

    ocr_image_sizes = []

    def capture_ocr(jpeg_bytes, filename):
        im = Image.open(io.BytesIO(jpeg_bytes))
        ocr_image_sizes.append(im.size)
        return ExtractionResult(
            questions=[ExtractedQuestion("Q?", ["A", "B"], answer_raw="A", question_number=1)],
            raw_text="Q1. Q?\nA) A ✅\nB) B",
            total_found=1,
            source_filename=filename,
        )

    with patch.object(router.vision_extractor, "is_available", return_value=True):
        with patch.object(router.vision_extractor, "extract_from_bytes", side_effect=capture_ocr):
            summary = router.ingest(pdf_bytes, "rotated.pdf")
            assert summary.success is True
            assert len(ocr_image_sizes) == 2
            # Page 1 (0 deg): 200 x 100
            assert ocr_image_sizes[0] == (200, 100)
            # Page 2 (90 deg clockwise): 100 x 200
            assert ocr_image_sizes[1] == (100, 200)


def test_hybrid_pdf_preserves_text_and_ocr():
    """Verify hybrid PDF preserves selectable text from text pages and executes OCR only for image pages."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)

    # Page 1: Selectable text
    c.drawString(72, 700, "Q1. What is the capital of Japan? 日本の首都はどこですか？")
    c.drawString(72, 680, "A. Tokyo / 東京 ✅")
    c.drawString(72, 660, "B. Kyoto / 京都")
    c.drawString(72, 640, "Answer: A")
    c.showPage()

    # Page 2: Scanned image (no selectable text)
    im = Image.new("RGB", (150, 150), color="blue")
    c.drawInlineImage(im, 72, 500, 150, 150)
    c.showPage()

    # Page 3: Selectable text
    c.drawString(72, 700, "Q3. What is 10 + 10?")
    c.drawString(72, 680, "A. 20 ✅")
    c.drawString(72, 660, "B. 30")
    c.drawString(72, 640, "Answer: A")
    c.showPage()

    c.save()

    pdf_bytes = buf.getvalue()
    router = DocumentRouter()

    # Mock VisionExtractor for page 2
    mock_p2_res = ExtractionResult(
        questions=[
            ExtractedQuestion(
                question_text="What is H2O?",
                options=["Water", "Fire"],
                answer_raw="Water",
                question_number=1,
            )
        ],
        raw_text="Q1. What is H2O?\nA) Water ✅\nB) Fire",
        total_found=1,
        source_filename="hybrid.pdf_page_2.jpg",
    )

    with patch.object(router.vision_extractor, "is_available", return_value=True):
        with patch.object(router.vision_extractor, "extract_from_bytes", return_value=mock_p2_res) as mock_ocr:
            summary = router.ingest(pdf_bytes, "hybrid.pdf")
            assert summary.success is True
            # Exactly 3 questions detected across the 3 pages
            assert summary.total_detected == 3
            # VisionExtractor must be called ONLY once (for page 2), NOT for text pages 1 and 3!
            assert mock_ocr.call_count == 1

            # Verify question ordering
            assert "capital of Japan" in summary.questions[0].question_text
            assert "What is H2O?" in summary.questions[1].question_text
            assert "What is 10 + 10?" in summary.questions[2].question_text


def test_router_shutdown_cleans_executor():
    """Verify DocumentRouter.shutdown cleanly shuts down the background ThreadPoolExecutor."""
    router = DocumentRouter()
    # Trigger executor lazy creation
    _ = router.executor
    assert router._executor is not None

    router.shutdown(wait=True)
    assert router._executor is None
