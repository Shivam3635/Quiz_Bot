"""Helper utilities for text formatting, bilingual quiz questions, and Telegram rich HTML."""

import html
import re
from typing import Optional

# English sentence with terminal punctuation followed by Devanagari (Hindi) character
ENG_TO_HINDI_RE = re.compile(r"([A-Za-z0-9][.!:;\-\'\"]+)\s+([\u0900-\u097F])")

# Devanagari sentence with terminal punctuation followed by English/Latin character
HINDI_TO_ENG_RE = re.compile(r"([\u0900-\u097F][।!?:;\-\'\"]+)\s+([A-Za-z])")

# Allowed Telegram HTML formatting tags
ALLOWED_TELEGRAM_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "tg-spoiler", "a"
}

# Regex to find HTML tag tokens: <tag>, </tag>, <tag attr="val">
TAG_TOKEN_RE = re.compile(r"(</?[a-zA-Z0-9_-]+(?:\s+[^>]*)?>)")
OPEN_TAG_NAME_RE = re.compile(r"^<([a-zA-Z0-9_-]+)(?:\s+[^>]*)?>$", re.IGNORECASE)
CLOSE_TAG_NAME_RE = re.compile(r"^</([a-zA-Z0-9_-]+)>$", re.IGNORECASE)


def format_bilingual_question_text(text: str) -> str:
    """
    Format bilingual questions so that the second language (e.g. Hindi)
    starts on a new line instead of continuing on the same line as English.
    The question mark '?' acts as the differentiator between English and
    Hindi question. When '?' occurs, all upcoming words are treated as the
    Hindi question and moved to the next line.
    """
    if not text:
        return text

    # Process line by line in case multi-line already exists
    lines = text.split("\n")
    formatted_lines: list[str] = []

    for line in lines:
        cleaned_line = line.strip()
        if not cleaned_line:
            continue

        # Check if '?' acts as differentiator with upcoming words on the same line
        matched_q = False
        curr = cleaned_line
        while True:
            q_match = re.search(r"\?+", curr)
            if q_match:
                start_pos, end_pos = q_match.span()
                upcoming_words = curr[end_pos:].strip()
                if upcoming_words:
                    matched_q = True
                    part = curr[:start_pos].rstrip() + curr[start_pos:end_pos]
                    formatted_lines.append(part)
                    curr = upcoming_words
                    continue
            break

        if matched_q:
            formatted_lines.append(curr)
            continue

        # Fallback 1: English -> Hindi transition on same line without '?'
        if ENG_TO_HINDI_RE.search(cleaned_line):
            cleaned_line = ENG_TO_HINDI_RE.sub(r"\1\n\2", cleaned_line, count=1)
            formatted_lines.extend([l.strip() for l in cleaned_line.split("\n") if l.strip()])
        # Fallback 2: Hindi -> English transition on same line without '?'
        elif HINDI_TO_ENG_RE.search(cleaned_line):
            cleaned_line = HINDI_TO_ENG_RE.sub(r"\1\n\2", cleaned_line, count=1)
            formatted_lines.extend([l.strip() for l in cleaned_line.split("\n") if l.strip()])
        else:
            formatted_lines.append(cleaned_line)

    return "\n".join(formatted_lines)


def convert_markdown_to_telegram_html(text: str) -> str:
    """
    Convert standard Markdown styling into Telegram-supported HTML tags.
    Handles:
      **bold** -> <b>bold</b>
      __underline__ -> <u>underline</u>
      *italic* -> <i>italic</i>
      `code` -> <code>code</code>
      ~~strikethrough~~ -> <s>strikethrough</s>
      ||spoiler|| -> <tg-spoiler>spoiler</tg-spoiler>
    """
    if not text:
        return text

    # Inline code first: `code`
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)

    # Bold: **text**
    text = re.sub(r"\*\*([^\*\n]+)\*\*", r"<b>\1</b>", text)

    # Underline: __text__
    text = re.sub(r"__([^_\n]+)__", r"<u>\1</u>", text)

    # Italic: *text* (word boundary or spaces to avoid clashing with asterisks)
    text = re.sub(r"(?<!\w)\*([^\*\n]+)\*(?!\w)", r"<i>\1</i>", text)

    # Strikethrough: ~~text~~
    text = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", text)

    # Spoiler: ||text||
    text = re.sub(r"\|\|([^|\n]+)\|\|", r"<tg-spoiler>\1</tg-spoiler>", text)

    return text


def sanitize_telegram_html(text: str) -> str:
    """
    Ensure text is 100% compliant with Telegram HTML parse mode.
    - Preserves allowed Telegram tags (b, strong, i, em, u, ins, s, strike, del, code, pre, tg-spoiler, a).
    - Safely escapes raw '<', '>', '&' outside of valid tags so math inequalities (e.g. x < 5)
      or unescaped symbols (e.g. A & B) do not cause Telegram entity parsing failures.
    - Automatically closes any unclosed open tags so entity parsing never fails.
    - Strips unsupported HTML tags while preserving their inner content.
    """
    if not text:
        return text

    tokens = TAG_TOKEN_RE.split(text)
    sanitized_parts: list[str] = []
    open_tag_stack: list[str] = []

    for token in tokens:
        if not token:
            continue

        open_match = OPEN_TAG_NAME_RE.match(token)
        close_match = CLOSE_TAG_NAME_RE.match(token)

        if open_match:
            tag_name = open_match.group(1).lower()
            if tag_name in ALLOWED_TELEGRAM_TAGS:
                if tag_name == "a":
                    # Only allow href attribute for <a>
                    href_match = re.search(r'href=["\']([^"\']+)["\']', token, re.IGNORECASE)
                    if href_match:
                        sanitized_parts.append(f'<a href="{html.escape(href_match.group(1), quote=True)}">')
                    else:
                        sanitized_parts.append(token)
                else:
                    sanitized_parts.append(f"<{tag_name}>")
                open_tag_stack.append(tag_name)
            # If tag is not allowed, ignore tag token (inner content will remain)
        elif close_match:
            tag_name = close_match.group(1).lower()
            if tag_name in ALLOWED_TELEGRAM_TAGS:
                sanitized_parts.append(f"</{tag_name}>")
                # Remove matching open tag from stack if present
                for idx in range(len(open_tag_stack) - 1, -1, -1):
                    if open_tag_stack[idx] == tag_name:
                        open_tag_stack.pop(idx)
                        break
            # If tag is not allowed, ignore closing tag token
        else:
            # Plain text token: escape '&', '<', '>'
            # Unescape first to prevent double-escaping (&amp; -> & -> &amp;)
            unescaped = html.unescape(token)
            escaped = html.escape(unescaped, quote=False)
            sanitized_parts.append(escaped)

    # Balance any tags left unclosed
    for tag_name in reversed(open_tag_stack):
        sanitized_parts.append(f"</{tag_name}>")

    return "".join(sanitized_parts)


def truncate_rich_text(text: str, max_chars: int) -> str:
    """
    Truncate text containing HTML tags to a maximum plain-text character count,
    ensuring all opened HTML tags are properly closed so entity parsing never fails.
    """
    if not text:
        return text

    # First check plain text length
    plain_text = re.sub(r"<[^>]+>", "", text)
    if len(plain_text) <= max_chars:
        return text

    target_plain_len = max_chars - 3  # reserve room for "..."
    if target_plain_len < 1:
        target_plain_len = 1

    tokens = TAG_TOKEN_RE.split(text)
    result_parts: list[str] = []
    current_plain_len = 0
    open_tag_stack: list[str] = []

    for token in tokens:
        if not token:
            continue

        open_match = OPEN_TAG_NAME_RE.match(token)
        close_match = CLOSE_TAG_NAME_RE.match(token)

        if open_match:
            tag_name = open_match.group(1).lower()
            if tag_name in ALLOWED_TELEGRAM_TAGS:
                result_parts.append(token)
                open_tag_stack.append(tag_name)
        elif close_match:
            tag_name = close_match.group(1).lower()
            if tag_name in ALLOWED_TELEGRAM_TAGS:
                result_parts.append(token)
                for idx in range(len(open_tag_stack) - 1, -1, -1):
                    if open_tag_stack[idx] == tag_name:
                        open_tag_stack.pop(idx)
                        break
        else:
            # Text chunk
            unescaped_chunk = html.unescape(token)
            needed = target_plain_len - current_plain_len

            if len(unescaped_chunk) <= needed:
                result_parts.append(token)
                current_plain_len += len(unescaped_chunk)
            else:
                # Slice unescaped text chunk safely
                sliced_chunk = unescaped_chunk[:needed]
                result_parts.append(html.escape(sliced_chunk, quote=False))
                current_plain_len += len(sliced_chunk)
                result_parts.append("...")
                break

    # Close any unclosed tags in reverse order
    for tag_name in reversed(open_tag_stack):
        result_parts.append(f"</{tag_name}>")

    return "".join(result_parts)


def strip_html_tags(text: Optional[str]) -> str:
    """Strip all HTML tags and unescape entities to return clean plain text."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    return html.unescape(clean).strip()


def format_rich_text_for_telegram(text: Optional[str], max_plain_length: int = 300) -> str:
    """
    Complete pipeline for question and explanation rich text:
    1. Converts Markdown styling (**bold**, `code`, etc.) to Telegram HTML.
    2. Sanitizes HTML tags, escapes rogue '<', '>', '&', and balances tags.
    3. Safely truncates to max_plain_length without leaving dangling unclosed tags.
    """
    if not text:
        return ""
    md_converted = convert_markdown_to_telegram_html(text)
    sanitized = sanitize_telegram_html(md_converted)
    return truncate_rich_text(sanitized, max_plain_length)
