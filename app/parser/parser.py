"""Robust parser for bulk Telegram quiz question input."""

import re
from typing import Optional
from dataclasses import dataclass, field
from app.parser.models import ParsedBatch, ParseError, QuizQuestion
from app.utils.devanagari_cleaner import clean_devanagari_text
from app.utils.helpers import format_bilingual_question_text
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Explicit question prefix: Q1. / Question 1: / प्रश्न 1: / Q1 / Question 1
EXPLICIT_Q_RE = re.compile(
    r"^(?:(?:Q|Question|प्रश्न)\s*(\d+)[\.\)\:\-\s]*)\s*(.*)$",
    re.IGNORECASE,
)

# Numeric question start: 1. / 1) / 1: / 1 -
NUMERIC_Q_RE = re.compile(
    r"^(\d+)[\.\:\-]\s+(.*)$",
)

# Option lines: A) / A. / A: / (A) / [A] / 1) / 1. / 1: / (1), with optional leading bullets (•, -, *, etc.)
OPTION_LINE_RE = re.compile(
    r"^[\s\t\•\●\○\▪\▫\◆\◇\-\*\–\—]*[\(\[]?([A-Za-z0-9])(?:[\)\]\.\:\-]\s*|\s+)(.+)$",
)

# Answer line: Answer: B / Ans: B / Correct: B / Correct Answer: B / उत्तर: B
ANSWER_LINE_RE = re.compile(
    r"^(?:Answer|Ans|Correct\s*Answer|Correct|उत्तर)\s*(?:(?:is|hai|होगा)?\s*[\:\-\=\.]\s*(.+)|(?:\s+(?:is|hai|होगा)?\s*([A-Za-z0-9\(\)\[\]\✅\✔️\✔\✓\*]+)))$",
    re.IGNORECASE,
)

# Explanation line: Explanation: ... / Expl: ... / Note: ... / व्याख्या: ...
EXPLANATION_LINE_RE = re.compile(
    r"^(?:Explanation|Expl|Note|Why|व्याख्या|स्पष्टीकरण)\s*[\:\-\=\.]\s*(.+)$",
    re.IGNORECASE,
)

# Inline answer indicators (checkmarks, asterisks, [x], (correct))
INLINE_CORRECT_TRAILING_RE = re.compile(
    r"[\s\(\[]*(?:✅|✔️|✔|✓|☑️|☑|√|\[x\]|\[X\]|\(correct\)|\(ans\)|\(answer\)|\(उत्तर\)|\(सही\)|\*)+[\s\)\]]*$",
    re.IGNORECASE,
)
INLINE_CORRECT_LEADING_RE = re.compile(
    r"^[\s\(\[]*(?:✅|✔️|✔|✓|☑️|☑|√|\[x\]|\[X\]|\(correct\)|\(ans\)|\(answer\)|\(उत्तर\)|\(सही\)|\*)+[\s\)\]]*",
    re.IGNORECASE,
)


@dataclass
class _DraftBlock:
    q_num: int
    raw_prefix: Optional[str] = None
    question_lines: list[str] = field(default_factory=list)
    options: list[tuple[str, str]] = field(default_factory=list)  # (key, text)
    answer_raw: Optional[str] = None
    explanation_raw: Optional[str] = None
    is_explicit_header: bool = False

    @property
    def is_ignorable_preamble(self) -> bool:
        """True if this block was created from unnumbered preamble/title text without options or answer."""
        return not self.is_explicit_header and len(self.options) == 0 and self.answer_raw is None


class QuizBotProParser:
    """Parser that converts raw text batches into structured QuizQuestion models."""

    def parse(self, raw_text: str) -> ParsedBatch:
        """Parse raw multi-question text into a ParsedBatch containing questions and errors."""
        if not raw_text or not raw_text.strip():
            return ParsedBatch(total_blocks_found=0)

        cleaned_raw = clean_devanagari_text(raw_text)
        lines = [line.strip() for line in cleaned_raw.splitlines()]
        drafts = self._collect_draft_blocks(lines)

        logger.info("Found %d draft question block(s) to process", len(drafts))

        questions: list[QuizQuestion] = []
        errors: list[ParseError] = []

        for draft in drafts:
            q_obj, err_obj = self._convert_draft_to_question(draft)
            if err_obj:
                errors.append(err_obj)
            elif q_obj:
                questions.append(q_obj)

        return ParsedBatch(
            questions=questions,
            errors=errors,
            total_blocks_found=len(drafts),
        )

    def _collect_draft_blocks(self, lines: list[str]) -> list[_DraftBlock]:
        """Group raw input lines into individual question draft blocks."""
        drafts: list[_DraftBlock] = []
        current: Optional[_DraftBlock] = None
        auto_index = 1

        for line in lines:
            if not line:
                continue

            explicit_match = EXPLICIT_Q_RE.match(line)
            numeric_match = NUMERIC_Q_RE.match(line)
            ans_match = ANSWER_LINE_RE.match(line)
            expl_match = EXPLANATION_LINE_RE.match(line)
            opt_match = OPTION_LINE_RE.match(line)

            # Case 1: Explicit Question header (Q1., Question 1:, प्रश्न 1:)
            if explicit_match:
                if current is not None and not current.is_ignorable_preamble:
                    drafts.append(current)
                num_str = explicit_match.group(1)
                q_num = int(num_str) if num_str else auto_index
                rest = explicit_match.group(2).strip()
                prefix = line[: len(line) - len(rest)].strip() if rest else line.strip()
                current = _DraftBlock(q_num=q_num, raw_prefix=prefix, is_explicit_header=True)
                if rest:
                    current.question_lines.append(rest)
                auto_index = q_num + 1
                continue

            # Case 2: Answer line
            # An answer line is only valid after options have started and must not be a question ending in ? or ؟.
            if ans_match and current is not None and not line.rstrip().endswith(("?", "؟")):
                if len(current.options) > 0:
                    current.answer_raw = (ans_match.group(1) or ans_match.group(2) or "").strip()
                    continue

            # Case 3: Explanation line
            if expl_match and current is not None and not line.rstrip().endswith(("?", "؟")):
                if len(current.options) > 0 or current.answer_raw is not None:
                    current.explanation_raw = expl_match.group(1).strip()
                    continue

            # Case 4: Numeric line at start of question (e.g. 1. What is..., 2. What is...)
            # A numeric line is treated as a NEW question if:
            # - No current block exists, OR
            # - Current block already has an answer, OR
            # - Current block already has >= 2 options
            if numeric_match:
                should_start_new_q = False
                if current is None:
                    should_start_new_q = True
                elif current.answer_raw is not None:
                    should_start_new_q = True
                elif len(current.options) >= 2:
                    should_start_new_q = True

                if should_start_new_q:
                    if current is not None and not current.is_ignorable_preamble:
                        drafts.append(current)
                    q_num = int(numeric_match.group(1))
                    rest = numeric_match.group(2).strip()
                    prefix = line[: len(line) - len(rest)].strip() if rest else line.strip()
                    current = _DraftBlock(q_num=q_num, raw_prefix=prefix, is_explicit_header=True)
                    if rest:
                        current.question_lines.append(rest)
                    auto_index = q_num + 1
                    continue

            # Case 5: Option line (A) ..., B) ..., 1) ..., etc.)
            if opt_match and current is not None and len(current.question_lines) > 0 and current.answer_raw is None:
                key = opt_match.group(1).upper()
                val = opt_match.group(2).strip()
                current.options.append((key, val))
                continue

            # Case 6: Plain text
            if current is None:
                # Started without an explicit Q1 / 1. header (single question paste or document title)
                current = _DraftBlock(q_num=auto_index, raw_prefix=f"Q{auto_index}.", is_explicit_header=False)
                current.question_lines.append(line)
                auto_index += 1
            elif current.answer_raw is not None:
                # Continued explanation line after answer
                if current.explanation_raw:
                    current.explanation_raw += " " + line
                else:
                    current.explanation_raw = line
            elif len(current.options) > 0:
                # Multi-line option text
                key, prev_val = current.options[-1]
                current.options[-1] = (key, prev_val + " " + line)
            else:
                # Continued question line
                current.question_lines.append(line)

        if current is not None and not current.is_ignorable_preamble:
            drafts.append(current)

        # Consolidate drafts: if a draft with the same q_num appears later
        # (e.g. user re-sent a corrected question), replace the earlier draft
        consolidated: list[_DraftBlock] = []
        q_num_map: dict[int, int] = {}

        for d in drafts:
            if d.q_num in q_num_map:
                existing_idx = q_num_map[d.q_num]
                consolidated[existing_idx] = d
            else:
                q_num_map[d.q_num] = len(consolidated)
                consolidated.append(d)

        return consolidated

    def _convert_draft_to_question(
        self, draft: _DraftBlock
    ) -> tuple[Optional[QuizQuestion], Optional[ParseError]]:
        """Validate and convert a raw draft block into a QuizQuestion model."""
        q_text = " ".join(draft.question_lines).strip()
        header_snippet = (q_text[:40] + "...") if len(q_text) > 40 else q_text

        # 1. Question text presence
        if not q_text:
            return None, ParseError(
                question_number=draft.q_num,
                raw_header=header_snippet,
                message="Question text is missing.",
            )

        # 2. Options presence & minimum count
        if len(draft.options) == 0:
            return None, ParseError(
                question_number=draft.q_num,
                raw_header=header_snippet,
                message="No options found. Must provide options like A), B), C), D).",
            )

        if len(draft.options) < 2:
            opt_strings = [opt[1] for opt in draft.options]
            return None, ParseError(
                question_number=draft.q_num,
                raw_header=header_snippet,
                message=f"Only {len(draft.options)} option found. Telegram quizzes require at least 2 options.",
                details=f"Options found: {', '.join(opt_strings)}",
            )

        # 3. Detect inline correct answer markers (e.g. ✅, [x], *) in options
        detected_inline_idx: Optional[int] = None
        cleaned_options: list[tuple[str, str]] = []

        for idx, (key, opt_text) in enumerate(draft.options):
            has_marker = bool(
                INLINE_CORRECT_TRAILING_RE.search(opt_text)
                or INLINE_CORRECT_LEADING_RE.search(opt_text)
            )
            cleaned_text = opt_text
            if has_marker:
                cleaned_text = INLINE_CORRECT_LEADING_RE.sub(
                    "", INLINE_CORRECT_TRAILING_RE.sub("", opt_text)
                ).strip()
                if detected_inline_idx is None:
                    detected_inline_idx = idx

            cleaned_options.append((key, cleaned_text))

        # Use inline detected answer if explicit answer line is missing
        if not draft.answer_raw and detected_inline_idx is not None:
            draft.answer_raw = draft.options[detected_inline_idx][0]

        # Check answer presence
        if not draft.answer_raw:
            return None, ParseError(
                question_number=draft.q_num,
                raw_header=header_snippet,
                message="Correct answer is missing (e.g. 'Answer: B' or marked with '✅').",
            )

        option_keys = [opt[0] for opt in cleaned_options]
        option_texts = [opt[1] for opt in cleaned_options]

        # 4. Resolve correct answer index
        correct_index = self._resolve_correct_index(draft.answer_raw, option_keys, option_texts)
        if correct_index is None:
            available_keys = ", ".join(option_keys)
            return None, ParseError(
                question_number=draft.q_num,
                raw_header=header_snippet,
                message=f"Correct answer '{draft.answer_raw}' does not match any available option.",
                details=f"Available options: {available_keys}",
            )

        try:
            final_q_text = format_bilingual_question_text(q_text)
            quiz_question = QuizQuestion(
                question=final_q_text,
                options=option_texts,
                correct_option=correct_index,
                explanation=draft.explanation_raw,
                question_number=draft.q_num,
                raw_prefix=draft.raw_prefix,
            )
            return quiz_question, None
        except Exception as exc:
            return None, ParseError(
                question_number=draft.q_num,
                raw_header=header_snippet,
                message=f"Validation error: {str(exc)}",
            )

    def _resolve_correct_index(
        self, answer_raw: str, option_keys: list[str], options: list[str]
    ) -> Optional[int]:
        """Resolve correct answer string into a 0-based option index."""
        cleaned_ans = answer_raw.strip().strip("().,[]'\"").strip()
        ans_upper = cleaned_ans.upper()

        # 1. Direct key match (e.g. 'B' in ['A', 'B', 'C', 'D'] or '2' in ['1', '2', '3'])
        if ans_upper in option_keys:
            return option_keys.index(ans_upper)

        # 2. Letter to alphabet index (A=0, B=1, C=2, D=3)
        if len(ans_upper) == 1 and "A" <= ans_upper <= "Z":
            idx = ord(ans_upper) - ord("A")
            if 0 <= idx < len(options):
                return idx

        # 3. Numeric answer (e.g. '2' -> option 2 = index 1)
        if cleaned_ans.isdigit():
            num = int(cleaned_ans)
            # 1-based index check
            if 1 <= num <= len(options):
                return num - 1
            # 0-based index check
            if 0 <= num < len(options):
                return num

        # 4. Text match with option contents (e.g. Answer: New Delhi)
        for i, opt in enumerate(options):
            if cleaned_ans.lower() == opt.lower():
                return i

        # 5. Answer starts with letter followed by option text (e.g. 'B) New Delhi')
        for i, (key, opt) in enumerate(zip(option_keys, options)):
            if ans_upper.startswith(key):
                return i

        return None


# Backwards compatible alias
BulkQuizParser = QuizBotProParser
