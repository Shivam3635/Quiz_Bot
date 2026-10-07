"""Devanagari text repair and normalization utilities.

Fixes common distortions in Hindi/Devanagari text caused by PDF font encoding bugs
(especially Microsoft Word exports with Nirmala UI) and font-spacing artifacts.
"""

import re

# Mapping of known corrupted words/tokens from Nirmala UI / Word PDF font CMap bugs
# to their correct Devanagari representations.
NIRMALA_WORD_REPLACEMENTS: dict[str, str] = {
    # Spaced out / broken variants
    "इन्फ ैं ट र ी": "इन्फैंट्री",
    "इन्फैं ट री": "इन्फैंट्री",
    "इन्फैं टरी": "इन्फैंट्री",
    "कट र गर": "ट्रिगर",
    "कटर गर": "ट्रिगर",
    "कटरगर": "ट्रिगर",
    "फायरर ं ग": "फायरिंग",
    "फायररंग": "फायरिंग",
    "राष्ट्र पकत": "राष्ट्रपति",
    "राष्ट्र ीय": "राष्ट्रीय",
    "सेक ं ड": "सेकंड",
    "क ं धे": "कंधे",
    "र ैं क": "रैंक",
    "अक्ट ू बर": "अक्टूबर",
    "नायड ू": "नायडू",
    "क े रल": "केरल",
    "राधाक ृ ष्णन": "राधाकृष्णन",
    "राधाक ृष्णन": "राधाकृष्णन",
    "की िापना": "की स्थापना",
    "कीिापना": "की स्थापना",
    "िापना": "स्थापना",
    # Corrupted words where glyph <012E> (ि matra) mapped to क
    "कबंदु": "बिंदु",
    "स्थिकत": "स्थिति",
    "ककतना": "कितना",
    "ककतने": "कितने",
    "ककतनी": "कितनी",
    "ककस": "किस",
    "कलए": "लिए",
    "कमनट": "मिनट",
    "कनशाना": "निशाना",
    "कनशाने": "निशाने",
    "प्रकशक्षण": "प्रशिक्षण",
    "शस्थि": "शक्ति",
    "कक्रया": "क्रिया",
    "महाकनदेशक": "महानिदेशक",
    "अकधकारी": "अधिकारी",
    "लेस्थिनेंट": "लेफ्टिनेंट",
    "कनथल": "कर्नल",
    "किगेकडयर": "ब्रिगेडियर",
    "जकलयांवाला": "जलियांवाला",
    "कदल्ली": "दिल्ली",
    "रचकयता": "रचयिता",
    "बंककम": "बंकिम",
    "सरोकजनी": "सरोजिनी",
    "कबहू": "बिहू",
    "कबहार": "बिहार",
    "प्रकसद्ध": "प्रसिद्ध",
    "कसंह": "सिंह",
    "कदवस": "दिवस",
    "कशक्षक": "शिक्षक",
    "कसतंबर": "सितंबर",
    "ककग्रा": "किग्रा",
    "ककसे": "किसे",
    # Corrupted reph and conjunct glyphs
    "वर्थ": "वर्ष",
    "पुरुर्": "पुरुष",
    "सुभार्": "सुभाष",
    "आदशथ": "आदर्श",
    "शौयथ": "शौर्य",
    "कतथव्य": "कर्तव्य",
    "सवोपरर": "सर्वोपरि",
    "पवथतीय": "पर्वतीय",
    "आिा": "आस्था",
    "र्ी": "थी",
    "र्े": "थे",
    "रवींद्रनार्": "रवींद्रनाथ",
}

# Regex to strip spaces before Devanagari combining vowel signs (matras),
# anusvara, candrabindu, visarga, nukta, and virama.
# Range covers:
# \u0901-\u0903: Candrabindu, Anusvara, Visarga
# \u093a-\u094f: Vowel signs (matras) and Virama
# \u0955-\u0957: Extended vowel signs
# \u0962-\u0963: Vocalic L / LL signs
DEVANAGARI_COMBINING_SPACE_RE = re.compile(
    r"\s+([\u0901-\u0903\u093a-\u094f\u0955-\u0957\u0962\u0963])"
)

# Regex to strip spaces after virama (halant) when immediately followed by a Devanagari consonant
DEVANAGARI_VIRAMA_SPACE_RE = re.compile(
    r"([\u094d])\s+([\u0915-\u0939])"
)


def clean_devanagari_text(text: str) -> str:
    """Normalize and fix distorted Devanagari / Hindi text.

    - Resolves Nirmala UI font CMap export corruptions (e.g. कबंदु -> बिंदु, स्थिकत -> स्थिति).
    - Removes spurious spacing before combining marks and matras (e.g. इन्फ ैं -> इन्फैं).
    - Recombines split syllables inside words.
    """
    if not text:
        return text

    # Quick check: only process if string contains Devanagari characters
    if not any("\u0900" <= ch <= "\u097f" for ch in text):
        return text

    result = text

    # 1. Apply known dictionary replacements first
    for bad, good in NIRMALA_WORD_REPLACEMENTS.items():
        if bad in result:
            result = result.replace(bad, good)

    # 2. Strip spurious spaces before combining signs and matras
    result = DEVANAGARI_COMBINING_SPACE_RE.sub(r"\1", result)

    # 3. Strip spaces after virama (halant) before next consonant (e.g. न् फ -> न्फ)
    result = DEVANAGARI_VIRAMA_SPACE_RE.sub(r"\1\2", result)

    # 4. Clean up any leftover duplicate horizontal spaces while preserving newlines
    result = re.sub(r"[ \t]+", " ", result)

    return result
