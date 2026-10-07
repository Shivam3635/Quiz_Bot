"""Unit tests for Devanagari / Hindi text cleaner and PDF/DOCX repair."""

import pytest
from app.parser.parser import BulkQuizParser
from app.utils.devanagari_cleaner import clean_devanagari_text


def test_user_reported_q10_hindi_distortion_fix():
    """Verify that Q10 with distorted Hindi options is cleanly repaired."""
    raw = (
        "Q10. The full form of MPI is: MPI का पूरा नाम क्या है?\n"
        "A. Mean Point of Impact (मीन पॉइंट ऑफ इम्पैक्ट) ✅\n"
        "B. Main Point of Impact (मुख्य प्रभाव कबंदु)\n"
        "C. Mean Point of Infantry (मीन पॉइंट ऑफ इन्फ ैं ट र ी)\n"
        "D. Main Position of Infantry (मुख्य स्थिकत इन्फ ैं ट र ी)\n"
        "Answer: A\n"
    )

    parser = BulkQuizParser()
    batch = parser.parse(raw)

    assert not batch.has_errors
    assert batch.valid_count == 1

    q = batch.questions[0]
    assert q.options[0] == "Mean Point of Impact (मीन पॉइंट ऑफ इम्पैक्ट)"
    assert q.options[1] == "Main Point of Impact (मुख्य प्रभाव बिंदु)"
    assert q.options[2] == "Mean Point of Infantry (मीन पॉइंट ऑफ इन्फैंट्री)"
    assert q.options[3] == "Main Position of Infantry (मुख्य स्थिति इन्फैंट्री)"
    assert q.correct_option == 0


def test_nirmala_ui_corrupted_words_repaired():
    """Verify corrupted words produced by Word NirmalaUI CMap are restored."""
    corrupted_sample = (
        "Q1. ककतना वजन है और स्थिकत क्या है? कबंदु और कनशाना क्या है?\n"
        "A) ककस वर्थ र्ी\n"
        "B) किस वर्ष थी ✅\n"
        "C) आदशथ और शौयथ\n"
        "D) कतथव्य और प्रकशक्षण\n"
    )

    parser = BulkQuizParser()
    batch = parser.parse(corrupted_sample)

    assert not batch.has_errors
    q = batch.questions[0]
    assert "कितना वजन है और स्थिति क्या है?" in q.question
    assert "बिंदु और निशाना क्या है?" in q.question
    assert q.options[0] == "किस वर्ष थी"
    assert q.options[1] == "किस वर्ष थी"
    assert q.options[2] == "आदर्श और शौर्य"
    assert q.options[3] == "कर्तव्य और प्रशिक्षण"


def test_devanagari_combining_mark_spaces_removed():
    """Verify spurious spaces before matras and combining signs are removed."""
    text = "सेक ं ड और क ं धे पर र ैं क और अक्ट ू बर तथा नायड ू और क े रल"
    cleaned = clean_devanagari_text(text)
    assert cleaned == "सेकंड और कंधे पर रैंक और अक्टूबर तथा नायडू और केरल"


def test_normal_hindi_preserved():
    """Verify clean Hindi text without distortions remains unchanged."""
    clean_text = "भारत का राष्ट्रीय गान जन गण मन है। इसकी रचना रवींद्रनाथ टैगोर ने की थी।"
    assert clean_devanagari_text(clean_text) == clean_text


def test_non_hindi_text_untouched():
    """English text without Devanagari is returned immediately without modification."""
    text = "What is the speed of light? 3x10^8 m/s"
    assert clean_devanagari_text(text) == text


def test_legacy_shifted_font_repaired():
    """Verify legacy shifted font words (फकस, वतवि, मनार्ा, गर्ा, िा) are repaired."""
    text = "ववश्व पर्ािवरण फिवस प्रत्र्ेक वर्ि फकस वतवि को मनार्ा जाता है? भारत में वन्यजीव संरक्षण अधिनियम फकस वर्ि लागू फकर्ा गर्ा िा?"
    cleaned = clean_devanagari_text(text)
    assert "विश्व" in cleaned
    assert "पर्यावरण" in cleaned
    assert "दिवस" in cleaned
    assert "प्रत्येक" in cleaned
    assert "वर्ष" in cleaned
    assert "किस" in cleaned
    assert "तिथि" in cleaned
    assert "मनाया" in cleaned
    assert "अधिनियम" in cleaned
    assert "किया" in cleaned
    assert "गया" in cleaned
    assert "था" in cleaned
