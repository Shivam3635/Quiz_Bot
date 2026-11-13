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
    r"^(\d+)[\.\)\:\-]\s+(.*)$",
)

# Option lines:
# - Latin/numeric: A) / A. / A: / (A) / [A] / 1) / 1. / 1: / (1) / A text
# - Hindi Devanagari: क) / ख) / ग) / घ) / (क) / (ख) / [क] / क. / क: / क-
# - Roman numerals: i) / (i) / ii) / (ii) / iii) / iv) / i. / ii.
OPTION_LINE_RE = re.compile(
    r"^[\s\t\•\●\○\▪\▫\◆\◇\-\*\–\—]*[\(\[]?((?:iv|ix|v?i{1,3}|IV|IX|V?I{1,3}|[A-Za-z0-9\u0905-\u0939]))(?:[\)\]\.\:\-]\s*|(?<=[A-Za-z0-9])\s+)(.+)$",
)

# Answer line: Answer: B / Ans: B / Correct: B / Correct Answer: B / उत्तर: B / उत्तर: ख / उत्तर ख
ANSWER_LINE_RE = re.compile(
    r"^(?:Answer|Ans|Correct\s*Answer|Correct|उत्तर)\s*(?:(?:is|hai|होगा)?\s*[\:\-\=\.]\s*(.+)|(?:\s+(?:is|hai|होगा)?\s*([A-Za-z0-9\u0905-\u0939\(\)\[\]\✅\✔️\✔\✓\□\🔲\■\▫\▣\▢\*]+)))$",
    re.IGNORECASE,
)

# Explanation line: Explanation: ... / Expl: ... / Note: ... / व्याख्या: ...
EXPLANATION_LINE_RE = re.compile(
    r"^(?:Explanation|Expl|Note|Why|व्याख्या|स्पष्टीकरण)\s*[\:\-\=\.]\s*(.+)$",
    re.IGNORECASE,
)

# Inline answer indicators (checkmarks, asterisks, boxes, [x], (correct))
INLINE_CORRECT_TRAILING_RE = re.compile(
    r"[\s\(\[]*(?:✅|✔️|✔|✓|☑️|☑|√|□|🔲|■|▫|▣|▢|\[x\]|\[X\]|\(correct\)|\(ans\)|\(answer\)|\(उत्तर\)|\(सही\)|\*)+[\s\)\]]*$",
    re.IGNORECASE,
)
INLINE_CORRECT_LEADING_RE = re.compile(
    r"^[\s\(\[]*(?:✅|✔️|✔|✓|☑️|☑|√|□|🔲|■|▫|▣|▢|\[x\]|\[X\]|\(correct\)|\(ans\)|\(answer\)|\(उत्तर\)|\(सही\)|\*)+[\s\)\]]*",
    re.IGNORECASE,
)
INLINE_CORRECT_ANYWHERE_RE = re.compile(
    r"[\s\(\[]*(?:✅|✔️|✔|✓|☑️|☑|√|□|🔲|■|▫|▣|▢)+[\s\)\]]*"
)

# Hindi Devanagari option sequences
HINDI_KA_KHA = ["क", "ख", "ग", "घ", "ङ", "च", "छ", "ज", "झ", "ञ"]
HINDI_A_BA_SA = ["अ", "ब", "स", "द", "य", "र", "ल", "व"]
HINDI_VOWELS = ["अ", "आ", "इ", "ई", "उ", "ऊ"]

# Roman numeral mapping
ROMAN_OPTION_KEYS: dict[str, int] = {
    "I": 0,
    "II": 1,
    "III": 2,
    "IV": 3,
    "V": 4,
    "VI": 5,
    "VII": 6,
    "VIII": 7,
    "IX": 8,
    "X": 9,
}


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
            # - Current block already has >= 2 options (unless continuing sequential numeric options)
            if numeric_match:
                should_start_new_q = False
                num_val = int(numeric_match.group(1))
                rest = numeric_match.group(2).strip()

                if current is None:
                    should_start_new_q = True
                elif current.answer_raw is not None:
                    should_start_new_q = True
                elif len(current.options) >= 2:
                    # Check if this line is actually a continuation of numeric options (e.g. 3) Mars, 4) Jupiter)
                    is_numeric_options = len(current.options) > 0 and all(k.isdigit() for k, _ in current.options)
                    is_sequential_next_option = is_numeric_options and (
                        num_val == len(current.options) + 1
                        or (current.options[0][0] == "0" and num_val == len(current.options))
                    )
                    if is_sequential_next_option:
                        should_start_new_q = False
                    else:
                        should_start_new_q = True

                if should_start_new_q:
                    if current is not None and not current.is_ignorable_preamble:
                        drafts.append(current)
                    q_num = num_val
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

        return drafts

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

        # 3. Detect inline correct answer markers (e.g. ✅, [x], *, □) in options
        detected_inline_idx: Optional[int] = None
        cleaned_options: list[tuple[str, str]] = []

        for idx, (key, opt_text) in enumerate(draft.options):
            has_marker = bool(
                INLINE_CORRECT_TRAILING_RE.search(opt_text)
                or INLINE_CORRECT_LEADING_RE.search(opt_text)
                or INLINE_CORRECT_ANYWHERE_RE.search(opt_text)
            )
            cleaned_text = opt_text
            if has_marker:
                cleaned_text = INLINE_CORRECT_ANYWHERE_RE.sub(" ", opt_text).strip()
                cleaned_text = re.sub(r"\s+", " ", cleaned_text)
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

    def _match_single_key(
        self, token: str, option_keys: list[str], options: list[str]
    ) -> Optional[int]:
        """Match a single key token against option keys or known numbering schemes."""
        if not token:
            return None
        tok = token.strip().strip("()[]{}.:-\"'").strip()
        if not tok:
            return None

        # 1. Direct match against option_keys (case-insensitive for Latin/Roman)
        upper_keys = [k.upper() for k in option_keys]
        if tok.upper() in upper_keys:
            return upper_keys.index(tok.upper())
        if tok in option_keys:
            return option_keys.index(tok)

        # 2. Devanagari Hindi option letters
        if tok in HINDI_KA_KHA:
            idx = HINDI_KA_KHA.index(tok)
            if 0 <= idx < len(options):
                return idx
        if tok in HINDI_A_BA_SA:
            idx = HINDI_A_BA_SA.index(tok)
            if 0 <= idx < len(options):
                return idx
        if tok in HINDI_VOWELS:
            idx = HINDI_VOWELS.index(tok)
            if 0 <= idx < len(options):
                return idx

        # 3. Numeric index (1-based e.g. '2' -> index 1, or 0-based '0' -> index 0)
        if tok.isdigit():
            num = int(tok)
            if 1 <= num <= len(options):
                return num - 1
            if 0 <= num < len(options):
                return num

        # 4. If current options are Roman numerals, Roman takes priority
        is_roman_options = any(k in ("I", "II", "III", "IV", "V") for k in upper_keys)
        if is_roman_options and tok.upper() in ROMAN_OPTION_KEYS:
            idx = ROMAN_OPTION_KEYS[tok.upper()]
            if 0 <= idx < len(options):
                return idx

        # 5. Latin letter (A-Z)
        if len(tok) == 1 and "A" <= tok.upper() <= "Z":
            idx = ord(tok.upper()) - ord("A")
            if 0 <= idx < len(options):
                return idx

        # 6. Roman numeral (e.g. ii, iii, iv) when options are letters or Hindi
        if tok.upper() in ROMAN_OPTION_KEYS:
            idx = ROMAN_OPTION_KEYS[tok.upper()]
            if 0 <= idx < len(options):
                return idx

        return None

    def _resolve_correct_index(
        self, answer_raw: str, option_keys: list[str], options: list[str]
    ) -> Optional[int]:
        """Resolve correct answer string into a 0-based option index."""
        if not answer_raw or not options:
            return None

        # Clean raw answer string of common trailing markers (✅, *, boxes, etc.)
        cleaned_ans = re.sub(
            r"[\s\(\[]*(?:✅|✔️|✔|✓|☑️|☑|√|□|🔲|■|▫|▣|▢|\[x\]|\[X\]|\(correct\)|\(ans\)|\(answer\)|\(उत्तर\)|\(सही\)|\*)+[\s\)\]]*",
            " ",
            answer_raw,
            flags=re.IGNORECASE,
        ).strip()
        if not cleaned_ans:
            cleaned_ans = answer_raw.strip()

        # Step 1: Strip optional prefix like "Option B", "Opt: B", "विकल्प ख", "Choice 2"
        prefix_match = re.match(
            r"^(?:Option|Opt|विकल्प|Choice)\s*[\:\-\.]?\s*(.+)$",
            cleaned_ans,
            re.IGNORECASE,
        )
        if prefix_match:
            inner = prefix_match.group(1).strip()
            # Try single key on inner (e.g. "B", "(B)", "ख", "2")
            idx = self._match_single_key(inner, option_keys, options)
            if idx is not None:
                return idx
            # Or if inner matches full option text
            for i, opt in enumerate(options):
                if inner.lower() == opt.lower().strip():
                    return i
            cleaned_ans = inner

        # Step 2: Try single key match on cleaned_ans (e.g. "B", "(B)", "[C]", "ख", "क)", "2", "iv")
        single_idx = self._match_single_key(cleaned_ans, option_keys, options)
        if single_idx is not None:
            return single_idx

        # Step 3: Full option text match (case-insensitive, whitespace-normalized)
        # e.g. Answer: Australia, Answer: New Delhi, Answer: All of the above
        norm_ans = " ".join(cleaned_ans.lower().split())
        for i, opt in enumerate(options):
            norm_opt = " ".join(opt.lower().split())
            if norm_ans == norm_opt or norm_ans.rstrip(".,;") == norm_opt.rstrip(".,;"):
                return i

        # Step 4: Delimited key + text (e.g. 'B) New Delhi', 'B. New Delhi', 'B - New Delhi', '(B) New Delhi', 'ख) नई दिल्ली')
        delim_match = re.match(
            r"^[\(\[]?((?:iv|ix|v?i{1,3}|IV|IX|V?I{1,3}|[A-Za-z0-9\u0905-\u0939]))[\)\]\.\:\-]\s*(.*)$",
            cleaned_ans,
            re.IGNORECASE,
        )
        if delim_match:
            key_cand = delim_match.group(1)
            idx = self._match_single_key(key_cand, option_keys, options)
            if idx is not None:
                return idx

        # Step 5: Key separated by space + text where text matches the option (e.g. 'B New Delhi')
        space_match = re.match(
            r"^[\(\[]?([A-Za-z0-9\u0905-\u0939])[\)\]]?\s+(.+)$",
            cleaned_ans,
            re.IGNORECASE,
        )
        if space_match:
            key_cand = space_match.group(1)
            text_cand = space_match.group(2).strip().lower()
            idx = self._match_single_key(key_cand, option_keys, options)
            if idx is not None and 0 <= idx < len(options):
                norm_opt = " ".join(options[idx].lower().split())
                if text_cand == norm_opt or text_cand in norm_opt or norm_opt in text_cand:
                    return idx

        # Note: We deliberately DO NOT use prefix matching like `ans_upper.startswith(key)`
        # because an answer like "Australia" must NEVER match Option A.
        return None


# Backwards compatible alias
BulkQuizParser = QuizBotProParser
