"""AI and heuristic bilingual option length compression service for Telegram Quiz Polls."""

import re
from dataclasses import dataclass, field
from typing import Optional
import httpx

from app.config.settings import TELEGRAM_OPTION_MAX_LENGTH, get_settings
from app.parser.models import QuizQuestion
from app.parser.validator import validate_option_length
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Common Hindi filler and verbose phrases mapped to concise, exact semantic synonyms
HINDI_REPLACEMENTS = [
    (r"(?<!\S)के\s+माध्यम\s+से(?!\S)", "द्वारा"),
    (r"(?<!\S)के\s+जरिये(?!\S)", "द्वारा"),
    (r"(?<!\S)के\s+जरिए(?!\S)", "द्वारा"),
    (r"(?<!\S)के\s+रूप\s+में\s+जाना\s+जाता\s+है(?!\S)", "कहलाता है"),
    (r"(?<!\S)कहा\s+जाता\s+है(?!\S)", "कहते हैं"),
    (r"(?<!\S)का\s+उपयोग\s+करते\s+हुए(?!\S)", "के उपयोग से"),
    (r"(?<!\S)का\s+प्रयोग\s+करते\s+हुए(?!\S)", "के प्रयोग से"),
    (r"(?<!\S)करने\s+की\s+प्रक्रिया(?!\S)", "करना"),
    (r"(?<!\S)के\s+संदर्भ\s+में(?!\S)", "में"),
    (r"(?<!\S)उपरोक्त\s+में\s+से\s+कोई\s+नहीं(?!\S)", "कोई नहीं"),
    (r"(?<!\S)इनमें\s+से\s+कोई\s+नहीं(?!\S)", "कोई नहीं"),
    (r"(?<!\S)उपरोक्त\s+में\s+से\s+सभी(?!\S)", "सभी"),
    (r"(?<!\S)उपरोक्त\s+सभी(?!\S)", "सभी"),
    (r"(?<!\S)प्रदान\s+करता\s+है(?!\S)", "देता है"),
    (r"(?<!\S)प्रदर्शित\s+करता\s+है(?!\S)", "दर्शाता है"),
]

# Common English filler phrases mapped to concise equivalents
ENGLISH_REPLACEMENTS = [
    (r"\bwhich\s+is\s+known\s+as\b", "known as"),
    (r"\bin\s+the\s+context\s+of\b", "in"),
    (r"\busing\s+a\s+heavy\b", "with heavy"),
    (r"\busing\s+a\b", "with"),
    (r"\busing\b", "with"),
    (r"\bdown\s+from\s+a\b", "from"),
    (r"\bdown\s+from\b", "from"),
    (r"\bprocess\s+of\b", ""),
    (r"\brefers\s+to\s+the\b", "is"),
    (r"\bis\s+defined\s+as\b", "is"),
    (r"\bnone\s+of\s+the\s+above\b", "None of these"),
    (r"\ball\s+of\s+the\s+above\b", "All of these"),
]


def extract_numbers_and_years(text: str) -> list[str]:
    """Extract numbers and years from text to ensure factual preservation."""
    return re.findall(r"\b\d+(?:\.\d+)?\b", text)


def verify_factual_preservation(original: str, shortened: str) -> bool:
    """Verify that vital factual elements (e.g. numbers, years) are not dropped or mutated."""
    orig_nums = extract_numbers_and_years(original)
    short_nums = extract_numbers_and_years(shortened)
    for num in orig_nums:
        if num not in short_nums:
            return False
    return True


def clean_condensed_spacing(text: str) -> str:
    """Clean up redundant spaces, brackets, and punctuation resulting from compression."""
    # Clean up whitespace inside parentheses: "( text )" -> "(text)"
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    # Clean up redundant spaces
    text = re.sub(r"[ \t]+", " ", text)
    # Clean up commas or spaces before closing parentheses
    text = re.sub(r",\s*\)", ")", text)
    return text.strip()


def compress_bilingual_option(option_text: str, limit: int = TELEGRAM_OPTION_MAX_LENGTH) -> Optional[str]:
    """
    Apply semantic heuristic compression to bilingual English + Hindi options.
    Preserves meaning, factual numbers, technical terms, and bilingual format without hard truncation.
    Returns compressed option string if <= limit, or None if safe compression under limit was not possible.
    """
    candidate = option_text.strip()
    if len(candidate) <= limit:
        return candidate

    # 1. Apply Hindi semantic replacements
    for pattern, replacement in HINDI_REPLACEMENTS:
        candidate = re.sub(pattern, replacement, candidate, flags=re.IGNORECASE)

    candidate = clean_condensed_spacing(candidate)
    if len(candidate) <= limit and verify_factual_preservation(option_text, candidate):
        return candidate

    # 2. Apply English semantic replacements
    for pattern, replacement in ENGLISH_REPLACEMENTS:
        candidate = re.sub(pattern, replacement, candidate, flags=re.IGNORECASE)

    candidate = clean_condensed_spacing(candidate)
    if len(candidate) <= limit and verify_factual_preservation(option_text, candidate):
        return candidate

    # 3. Check for bilingual parenthesis structure: "English (Hindi)"
    match = re.match(r"^(.*?)\s*\((.*?)\)$", candidate)
    if match:
        eng_part, hin_part = match.group(1).strip(), match.group(2).strip()

        # Condense English articles: "a ", "an ", "the " where grammatically optional in quiz options
        eng_compact = re.sub(r"\b(?:a|an|the)\s+", "", eng_part, flags=re.IGNORECASE).strip()
        compact_candidate = clean_condensed_spacing(f"{eng_compact} ({hin_part})")
        if len(compact_candidate) <= limit and verify_factual_preservation(option_text, compact_candidate):
            return compact_candidate

        # Try further compacting Hindi auxiliary particles if still over limit
        hin_compact = re.sub(r"\bके\s+लिए\b", "हेतु", hin_part)
        compact_candidate = clean_condensed_spacing(f"{eng_compact} ({hin_compact})")
        if len(compact_candidate) <= limit and verify_factual_preservation(option_text, compact_candidate):
            return compact_candidate

    # Return candidate only if it actually satisfies the limit and preserved facts
    if len(candidate) <= limit and verify_factual_preservation(option_text, candidate):
        return candidate

    return None


@dataclass
class ShortenResult:
    """Outcome of shortening a single option."""

    original_text: str
    shortened_text: str
    original_length: int
    shortened_length: int
    success: bool
    method: str  # "unchanged", "gemini", "openai", "heuristic", "failed"
    error_message: Optional[str] = None
    question_index: Optional[int] = None
    option_letter: Optional[str] = None
    option_index: Optional[int] = None


@dataclass
class BatchShortenResult:
    """Consolidated outcome of batch option shortening."""

    total_checked: int
    oversized_count: int
    successful_count: int
    failed_count: int
    results: list[ShortenResult] = field(default_factory=list)

    @property
    def is_all_valid(self) -> bool:
        """True if zero options remain oversized."""
        return self.failed_count == 0


class OptionShortenerService:
    """Intelligently shortens bilingual quiz options to comply with Telegram's 100-char limit."""

    PROMPT_TEMPLATE = (
        "Shorten this bilingual quiz option to 100 characters or fewer.\n\n"
        "Requirements:\n"
        "- Preserve the exact meaning.\n"
        "- Preserve both English and Hindi where possible.\n"
        "- Do not remove important technical terms.\n"
        "- Do not change the answer's meaning.\n"
        "- Do not add explanations.\n"
        "- Do not invent information.\n"
        "- Make the result natural and readable.\n"
        "- Return only the shortened option.\n\n"
        "Option: {option_text}"
    )

    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    async def _call_gemini(self, prompt: str, api_key: str) -> Optional[str]:
        """Call Google Gemini API using httpx."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 100,
            },
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"].strip()
            else:
                logger.warning("Gemini API call returned status %s: %s", resp.status_code, resp.text[:200])
        return None

    async def _call_openai(self, prompt: str, api_key: str) -> Optional[str]:
        """Call OpenAI-compatible Chat Completions API using httpx."""
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 100,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
            else:
                logger.warning("OpenAI API call returned status %s: %s", resp.status_code, resp.text[:200])
        return None

    def _clean_ai_output(self, raw_text: str) -> str:
        """Strip surrounding quotes, markdown fences, or labels produced by AI."""
        text = raw_text.strip()
        # Remove markdown code blocks if any
        if text.startswith("```") and text.endswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]).strip()

        # Remove common prefixes like 'Shortened option:', 'Option:', etc.
        text = re.sub(r"^(?:shortened(?:\s+option)?|option)\s*:\s*", "", text, flags=re.IGNORECASE)
        # Strip surrounding quotes
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        return text

    async def shorten_option(
        self,
        option_text: str,
        question_text: str = "",
        question_idx: int = 0,
        option_letter: str = "",
        option_idx: Optional[int] = None,
    ) -> ShortenResult:
        """
        Shorten an option to <= 100 characters using AI with heuristic fallback.
        Never performs hard truncation.
        """
        original = option_text.strip()
        orig_len = len(original)

        # Already valid
        if orig_len <= TELEGRAM_OPTION_MAX_LENGTH:
            return ShortenResult(
                original_text=original,
                shortened_text=original,
                original_length=orig_len,
                shortened_length=orig_len,
                success=True,
                method="unchanged",
                question_index=question_idx,
                option_letter=option_letter,
                option_index=option_idx,
            )

        # 1. Attempt AI compression if configured
        if self.settings.AI_SHORTENER_ENABLED:
            prompt = self.PROMPT_TEMPLATE.format(option_text=original)
            if question_text:
                prompt += f"\nQuestion Context: {question_text.strip()}"

            ai_result_raw = None
            method = "none"

            try:
                if self.settings.GEMINI_API_KEY:
                    ai_result_raw = await self._call_gemini(prompt, self.settings.GEMINI_API_KEY)
                    method = "gemini"
                elif self.settings.OPENAI_API_KEY:
                    ai_result_raw = await self._call_openai(prompt, self.settings.OPENAI_API_KEY)
                    method = "openai"
            except Exception as ai_err:
                logger.warning("AI shortening failed with exception: %s. Falling back to heuristic.", ai_err)

            if ai_result_raw:
                cleaned_ai = self._clean_ai_output(ai_result_raw)
                val_res = validate_option_length(cleaned_ai)

                # Attempt second pass if AI response was still slightly > 100
                if not val_res.valid and val_res.length > TELEGRAM_OPTION_MAX_LENGTH:
                    strict_prompt = (
                        f"Shorten this bilingual quiz option strictly to 100 characters or fewer.\n"
                        f"Current text is {val_res.length} characters (too long).\n"
                        f"Preserve meaning and numbers.\n\nOption: {cleaned_ai}"
                    )
                    try:
                        if method == "gemini" and self.settings.GEMINI_API_KEY:
                            second_raw = await self._call_gemini(strict_prompt, self.settings.GEMINI_API_KEY)
                        elif method == "openai" and self.settings.OPENAI_API_KEY:
                            second_raw = await self._call_openai(strict_prompt, self.settings.OPENAI_API_KEY)
                        else:
                            second_raw = None

                        if second_raw:
                            cleaned_ai = self._clean_ai_output(second_raw)
                            val_res = validate_option_length(cleaned_ai)
                    except Exception as retry_err:
                        logger.debug("AI retry exception: %s", retry_err)

                # Validate AI output against limit and facts
                if val_res.valid and verify_factual_preservation(original, cleaned_ai):
                    logger.info(
                        "OPTION_AUTO_SHORTEN question=%s option=%s original_length=%d new_length=%d status=success method=%s",
                        question_idx or "?",
                        option_letter or "?",
                        orig_len,
                        val_res.length,
                        method,
                    )
                    return ShortenResult(
                        original_text=original,
                        shortened_text=cleaned_ai,
                        original_length=orig_len,
                        shortened_length=val_res.length,
                        success=True,
                        method=method,
                        question_index=question_idx,
                        option_letter=option_letter,
                        option_index=option_idx,
                    )

        # 2. Heuristic semantic compression fallback
        heuristic_candidate = compress_bilingual_option(original, limit=TELEGRAM_OPTION_MAX_LENGTH)
        if heuristic_candidate and len(heuristic_candidate) <= TELEGRAM_OPTION_MAX_LENGTH:
            short_len = len(heuristic_candidate)
            logger.info(
                "OPTION_AUTO_SHORTEN question=%s option=%s original_length=%d new_length=%d status=success method=heuristic",
                question_idx or "?",
                option_letter or "?",
                orig_len,
                short_len,
            )
            return ShortenResult(
                original_text=original,
                shortened_text=heuristic_candidate,
                original_length=orig_len,
                shortened_length=short_len,
                success=True,
                method="heuristic",
                question_index=question_idx,
                option_letter=option_letter,
                option_index=option_idx,
            )

        # 3. Compression could not safely reduce under 100 characters without truncation
        logger.warning(
            "OPTION_AUTO_SHORTEN question=%s option=%s original_length=%d status=failed error='Could not compress under 100 chars'",
            question_idx or "?",
            option_letter or "?",
            orig_len,
        )
        return ShortenResult(
            original_text=original,
            shortened_text=original,
            original_length=orig_len,
            shortened_length=orig_len,
            success=False,
            method="failed",
            error_message=(
                f"Unable to automatically shorten this option safely.\n"
                f"Q{question_idx or '?'} → Option {option_letter or '?'}\n"
                f"Current length: {orig_len}/{TELEGRAM_OPTION_MAX_LENGTH}\n"
                f"Please edit the option manually."
            ),
            question_index=question_idx,
            option_letter=option_letter,
            option_index=option_idx,
        )

    async def shorten_all_oversized_options(self, questions: list[QuizQuestion]) -> BatchShortenResult:
        """Find and shorten all options exceeding 100 characters across a list of questions."""
        results: list[ShortenResult] = []
        oversized_count = 0
        successful_count = 0
        failed_count = 0

        for q_idx, q in enumerate(questions, start=1):
            for opt_idx, opt in enumerate(q.options):
                opt_letter = chr(ord("A") + opt_idx)
                if len(opt.strip()) > TELEGRAM_OPTION_MAX_LENGTH:
                    oversized_count += 1
                    res = await self.shorten_option(
                        option_text=opt,
                        question_text=q.question,
                        question_idx=q_idx,
                        option_letter=opt_letter,
                        option_idx=opt_idx,
                    )
                    results.append(res)
                    if res.success:
                        successful_count += 1
                    else:
                        failed_count += 1

        return BatchShortenResult(
            total_checked=sum(len(q.options) for q in questions),
            oversized_count=oversized_count,
            successful_count=successful_count,
            failed_count=failed_count,
            results=results,
        )


# Global singleton instance
shortener_service = OptionShortenerService()
