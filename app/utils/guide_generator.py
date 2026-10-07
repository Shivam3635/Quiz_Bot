"""Generator for the comprehensive QuizBotPro Official PDF User Guide."""

import io
from typing import Optional
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
    HRFlowable,
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Canvas that computes total pages dynamically for a 'Page X of Y' footer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#718096"))

        # Top Running Header (from page 2 onwards)
        if self._pageNumber > 1:
            self.drawString(54, 11 * inch - 36, "QuizBotPro — Official User Guide & Reference Manual")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(54, 11 * inch - 40, 8.5 * inch - 54, 11 * inch - 40)

        # Bottom Running Footer
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(8.5 * inch - 54, 30, page_text)
        self.drawString(54, 30, "QuizBotPro • Telegram Bulk Quiz Poll Creator & Live Host")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(54, 42, 8.5 * inch - 54, 42)

        self.restoreState()


def build_user_guide_pdf_bytes() -> bytes:
    """Build and return binary bytes of the comprehensive QuizBotPro PDF Guide."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    base_styles = getSampleStyleSheet()

    # Custom Palettes
    PRIMARY = colors.HexColor("#1A365D")    # Dark Navy
    SECONDARY = colors.HexColor("#2B6CB0")  # Royal Blue
    ACCENT = colors.HexColor("#2C5282")     # Slate Blue
    SUCCESS = colors.HexColor("#22543D")    # Forest Green
    TEXT_COLOR = colors.HexColor("#2D3748") # Dark Grey Body
    MUTED_TEXT = colors.HexColor("#4A5568") # Mid Grey
    BG_LIGHT = colors.HexColor("#F7FAFC")   # Soft Off-White
    BG_ACCENT = colors.HexColor("#EDF2F7")  # Light Grey
    CODE_BG = colors.HexColor("#2D3748")    # Code block dark

    # Typography Styles
    title_style = ParagraphStyle(
        "CoverTitle",
        parent=base_styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=30,
        textColor=PRIMARY,
        spaceAfter=6,
    )

    subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=MUTED_TEXT,
        spaceAfter=14,
    )

    h1_style = ParagraphStyle(
        "GuideH1",
        parent=base_styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=20,
        textColor=PRIMARY,
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "GuideH2",
        parent=base_styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=SECONDARY,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "GuideBody",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=TEXT_COLOR,
        spaceAfter=6,
    )

    bullet_style = ParagraphStyle(
        "GuideBullet",
        parent=body_style,
        leftIndent=12,
        firstLineIndent=-10,
        spaceAfter=4,
    )

    code_style = ParagraphStyle(
        "GuideCode",
        parent=base_styles["Normal"],
        fontName="Courier",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#EDF2F7"),
        backColor=CODE_BG,
        borderPadding=8,
        spaceBefore=4,
        spaceAfter=8,
    )

    callout_style = ParagraphStyle(
        "GuideCallout",
        parent=base_styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=13,
        textColor=ACCENT,
        backColor=BG_LIGHT,
        borderColor=SECONDARY,
        borderWidth=1,
        borderPadding=6,
        spaceBefore=4,
        spaceAfter=6,
    )

    th_style = ParagraphStyle(
        "GuideTH",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.white,
    )

    td_style = ParagraphStyle(
        "GuideTD",
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=TEXT_COLOR,
    )

    td_code_style = ParagraphStyle(
        "GuideTDCode",
        fontName="Courier-Bold",
        fontSize=8.5,
        leading=11,
        textColor=PRIMARY,
    )

    story = []

    # ==========================================
    # HEADER / BANNER
    # ==========================================
    story.append(Paragraph("QuizBotPro — Complete User Guide", title_style))
    story.append(Paragraph("Official Manual & Reference • High-Speed Bulk Quiz Poll Creation for Telegram", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=PRIMARY, spaceAfter=14))

    # ==========================================
    # 1. WHAT IS QUIZBOTPRO?
    # ==========================================
    story.append(Paragraph("1. What is QuizBotPro?", h1_style))
    story.append(Paragraph(
        "<b>QuizBotPro</b> is a powerful, high-performance Telegram bot engineered to automate the creation, "
        "customization, and hosting of native Telegram Quiz Polls. Instead of manually tapping buttons to add "
        "questions one by one, QuizBotPro allows educators, community leaders, and quiz masters to paste "
        "<b>hundreds of questions in seconds</b> or upload ready-made documents (Excel, Word, CSV, PDF).",
        body_style,
    ))
    story.append(Paragraph(
        "With built-in support for <b>bilingual (English + Hindi) formatting</b>, interactive broken question repair, "
        "configurable timers, channel broadcasting, and a live group countdown battle mode, QuizBotPro is the complete "
        "all-in-one quiz engine for Telegram.",
        body_style,
    ))

    # ==========================================
    # 2. KEY FEATURES AT A GLANCE
    # ==========================================
    story.append(Paragraph("2. Key Features at a Glance", h1_style))
    features = [
        ("⚡ <b>Blazing Fast Bulk Creation:</b>", "Create and publish 50–100+ native Telegram quiz polls in under 10 seconds."),
        ("📁 <b>Multi-Format File Ingestion:</b>", "Upload Excel (.xlsx), CSV (.csv), Word (.docx), PDF (.pdf), or plain text."),
        ("🌐 <b>Automated Bilingual Formatting:</b>", "Intelligently formats English and Hindi dual-language questions cleanly."),
        ("🛠️ <b>Interactive Broken Question Repair:</b>", "If 1 question has a typo, fix only that question without losing the quiz."),
        ("📦 <b>Multi-Part Input Session:</b>", "Send multiple files or long text chunks across messages; combine with 1 tap."),
        ("⏱️ <b>Customizable Quiz Parameters:</b>", "Default 15s timer (configurable: 10s–300s), Public/Anonymous voting, Header Banners."),
        ("⚔️ <b>Live Group Quiz Battles:</b>", "Host interactive timed quizzes in groups with live auto-advance and real-time leaderboards."),
        ("📚 <b>My Quizzes Dashboard:</b>", "Manage saved quiz sets with 1-click re-publishing to any channel or chat via /myquizzes."),
    ]
    for feat_title, feat_desc in features:
        story.append(Paragraph(f"• {feat_title} {feat_desc}", bullet_style))

    story.append(Spacer(1, 8))

    # ==========================================
    # 3. QUIZ CREATION WORKFLOW (STEP-BY-STEP)
    # ==========================================
    story.append(Paragraph("3. Step-by-Step Quiz Creation Flow", h1_style))
    steps = [
        ("Step 1: Start Creation Flow", "Send <b>/newquiz</b> or tap <b>📦 Bulk Create</b> in the main menu."),
        ("Step 2: Title & Description", "Enter a title for your quiz set, or tap <b>⏭️ Skip</b> for a timestamped default title. Add an optional description."),
        ("Step 3: Send Questions or Documents", "Paste raw text (Q1, options, answer) OR upload documents (Excel sheet, Word docx, or PDF). You can send multiple files sequentially."),
        ("Step 4: Finalize Input", "Tap <b>✅ Done</b> once you have sent all your questions or files."),
        ("Step 5: Error Correction (if any)", "If any question has missing options or an unmarked answer, the bot prompts you to fix or drop only that broken item."),
        ("Step 6: Configure Settings", "Toggle parameters on the Settings Dashboard: <b>Timer</b> (15s default), <b>Mode</b> (Public/Anonymous), <b>Destination Channel</b>, and <b>Header Banner</b>."),
        ("Step 7: Preview & Publish", "Review interactive polls in preview mode, then tap <b>🚀 Publish Quiz</b> to post all questions instantly!"),
    ]
    for s_title, s_desc in steps:
        story.append(Paragraph(f"<b>{s_title}:</b> {s_desc}", bullet_style))

    story.append(Spacer(1, 8))

    # ==========================================
    # 4. SUPPORTED FORMATS WITH EXAMPLES
    # ==========================================
    story.append(Paragraph("4. Supported Formats & Live Examples", h1_style))

    story.append(Paragraph("Format A: Direct Text (Standard or Inline Checkmark)", h2_style))
    story.append(Paragraph(
        "You can mark the correct answer with an explicit <code>Answer: B</code> line or with an inline checkmark (<code>✅</code>, <code>✓</code>, <code>□</code>, <code>*</code>):",
        body_style,
    ))
    text_example = (
        "Q1. What is the capital of India?<br/>"
        "A) Mumbai<br/>"
        "B) New Delhi ✅<br/>"
        "C) Kolkata<br/>"
        "D) Chennai<br/>"
        "Explanation: New Delhi was declared capital in 1911.<br/><br/>"
        "Q2. What is the chemical formula for water?<br/>"
        "• A. CO2<br/>"
        "• B. H2O<br/>"
        "• C. NaCl<br/>"
        "• D. O2<br/>"
        "Answer: B"
    )
    story.append(Paragraph(text_example, code_style))

    story.append(Paragraph("Format B: Bilingual (English + Hindi)", h2_style))
    story.append(Paragraph(
        "Bilingual questions with question marks (<code>?</code>) are automatically formatted with clean paragraph breaks:",
        body_style,
    ))
    bilingual_example = (
        "Q1. Where is the headquarters of NCC located? एनसीसी का मुख्यालय कहाँ स्थित है?<br/>"
        "A) Mumbai / मुंबई<br/>"
        "B) Lucknow / लखनऊ<br/>"
        "C) New Delhi / नई दिल्ली ✓<br/>"
        "D) Pune / पुणे"
    )
    story.append(Paragraph(bilingual_example, code_style))

    story.append(Paragraph("Format C: Spreadsheets (Excel .xlsx & CSV .csv)", h2_style))
    story.append(Paragraph(
        "Download our ready-to-use template via <b>/template</b>. The bot automatically detects standard headers:",
        body_style,
    ))

    # Excel Table Example
    excel_headers = [Paragraph(h, th_style) for h in ["Question", "Option A", "Option B", "Option C", "Option D", "Answer"]]
    excel_r1 = [Paragraph(c, td_style) for c in ["Full form of MPI?", "Mean Point", "Mean Impact", "Mean Point of Impact", "Main Position", "C"]]
    excel_r2 = [Paragraph(c, td_style) for c in ["Capital of France?", "Berlin", "Paris", "Rome", "Madrid", "B"]]
    t_data = [excel_headers, excel_r1, excel_r2]
    sheet_table = Table(t_data, colWidths=[130, 70, 70, 110, 80, 44])
    sheet_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(sheet_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Format D: Word (.docx) & PDF Documents (.pdf)", h2_style))
    story.append(Paragraph(
        "• <b>Word (.docx):</b> Questions typed as standard paragraphs or formatted in Microsoft Word tables.<br/>"
        "• <b>PDF (.pdf):</b> Standard text PDFs, multi-page question papers, and bulleted quiz notes. "
        "Promotional footers (YouTube links, watermarks, page numbers) are automatically filtered out.",
        body_style,
    ))

    story.append(Spacer(1, 8))

    # ==========================================
    # 5. QUIZBOTPRO VS OFFICIAL TELEGRAM @QUIZBOT
    # ==========================================
    story.append(Paragraph("5. QuizBotPro vs Official Telegram @QuizBot", h1_style))
    story.append(Paragraph(
        "Why top educators and community creators choose QuizBotPro over the standard Telegram @QuizBot:",
        body_style,
    ))

    comp_headers = [
        Paragraph("Feature", th_style),
        Paragraph("Official Telegram @QuizBot", th_style),
        Paragraph("QuizBotPro", th_style),
    ]
    comp_rows = [
        [
            Paragraph("<b>Bulk Ingestion</b>", td_style),
            Paragraph("❌ None. Must type/tap each question and option manually 1-by-1.", td_style),
            Paragraph("✅ <b>Instant.</b> Paste 100+ questions or upload full files at once.", td_style),
        ],
        [
            Paragraph("<b>File Upload Support</b>", td_style),
            Paragraph("❌ Text input in chat only.", td_style),
            Paragraph("✅ <b>Excel, CSV, Word (.docx), and PDF</b> files fully supported.", td_style),
        ],
        [
            Paragraph("<b>Bilingual Support</b>", td_style),
            Paragraph("❌ Manual copy-pasting, messy text formatting.", td_style),
            Paragraph("✅ <b>Smart bilingual split</b> (English + Hindi) with auto-clean.", td_style),
        ],
        [
            Paragraph("<b>Error Handling</b>", td_style),
            Paragraph("❌ If you make a mistake, you must restart from scratch.", td_style),
            Paragraph("✅ <b>Interactive Repair:</b> Edit or drop only broken questions.", td_style),
        ],
        [
            Paragraph("<b>Live Group Battles</b>", td_style),
            Paragraph("❌ Basic forwarded polls without real-time synced timer.", td_style),
            Paragraph("✅ <b>Live Game Host:</b> Synced countdown, auto-advance, live scoreboard.", td_style),
        ],
        [
            Paragraph("<b>Quiz Management</b>", td_style),
            Paragraph("❌ Lost in chat history, hard to find and reuse.", td_style),
            Paragraph("✅ <b>Dashboard (/myquizzes):</b> 1-click re-publish to any channel.", td_style),
        ],
        [
            Paragraph("<b>Creation Time</b>", td_style),
            Paragraph("⏱️ 20 to 35 minutes for 50 questions.", td_style),
            Paragraph("⚡ <b>Under 10 seconds</b> for 50 questions.", td_style),
        ],
    ]
    comp_table = Table([comp_headers] + comp_rows, colWidths=[120, 185, 199])
    comp_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(comp_table)
    story.append(Spacer(1, 10))

    # ==========================================
    # 6. COMMANDS & SHORTCUTS REFERENCE
    # ==========================================
    story.append(Paragraph("6. Quick Command Reference", h1_style))
    cmd_data = [
        [Paragraph("Command", th_style), Paragraph("Description", th_style)],
        [Paragraph("/newquiz", td_code_style), Paragraph("Initiate creation of a new bulk quiz set (text or document upload).", td_style)],
        [Paragraph("/myquizzes", td_code_style), Paragraph("Open your personal quiz dashboard to preview, manage, and 1-click re-publish quizzes.", td_style)],
        [Paragraph("/startquiz", td_code_style), Paragraph("Launch a synchronized live multiplayer quiz battle in a group chat.", td_style)],
        [Paragraph("/stopquiz", td_code_style), Paragraph("Stop an active live group quiz tournament.", td_style)],
        [Paragraph("/template", td_code_style), Paragraph("Download the official sample Excel (.xlsx) quiz spreadsheet template.", td_style)],
        [Paragraph("/guide", td_code_style), Paragraph("Download this complete PDF User Guide & Manual directly to your device.", td_style)],
        [Paragraph("/help", td_code_style), Paragraph("Display clear, quick in-app help and instructions.", td_style)],
        [Paragraph("/cancel", td_code_style), Paragraph("Cancel the current quiz creation session.", td_style)],
    ]
    cmd_table = Table(cmd_data, colWidths=[110, 394])
    cmd_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SECONDARY),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(cmd_table)
    story.append(Spacer(1, 10))

    # ==========================================
    # 7. PRO TIPS FOR CHANNEL & GROUP ADMINS
    # ==========================================
    story.append(Paragraph("7. Pro Tips for Channel & Group Admins", h1_style))
    tips = [
        ("📢 <b>Publishing to Channels:</b>", "Ensure QuizBotPro is added as an <b>Administrator</b> in your Telegram channel with the <i>'Post Messages'</i> permission enabled."),
        ("👥 <b>Group Live Battles:</b>", "Add QuizBotPro to your study group or community chat. Anyone with group permissions can run <b>/startquiz</b> to initiate an interactive competition!"),
        ("⏱️ <b>Default Settings:</b>", "By default, quizzes are created with <b>Public Voting (OFF)</b> and a <b>15-second timer</b>. You can customize the timer (10s–300s) anytime during creation or from your dashboard."),
    ]
    for t_title, t_desc in tips:
        story.append(Paragraph(f"• {t_title} {t_desc}", bullet_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buf.getvalue()
