"""Comprehensive tests for QuizBotPro parser."""

import pytest
from app.parser.parser import BulkQuizParser


@pytest.fixture
def parser() -> BulkQuizParser:
    """Fixture providing BulkQuizParser instance."""
    return BulkQuizParser()


def test_basic_format(parser: BulkQuizParser):
    """Test parsing a single question with standard Q1. and A) format."""
    text = """
Q1. What is the capital of India?
A) Mumbai
B) New Delhi
C) Kolkata
D) Chennai
Answer: B
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 1
    q = result.questions[0]
    assert q.question == "What is the capital of India?"
    assert len(q.options) == 4
    assert q.options[1] == "New Delhi"
    assert q.correct_option == 1
    assert q.correct_letter == "B"
    assert q.explanation is None


def test_multiple_questions(parser: BulkQuizParser):
    """Test parsing multiple questions in one batch."""
    text = """
Q1. What is the capital of India?
A) Mumbai
B) New Delhi
C) Kolkata
D) Chennai
Answer: B

Q2. Which language is used for web styling?
A) Python
B) Java
C) CSS
D) C++
Answer: C
Explanation: CSS stands for Cascading Style Sheets.
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 2
    assert result.total_blocks_found == 2

    # Check Q1
    assert result.questions[0].correct_letter == "B"

    # Check Q2
    q2 = result.questions[1]
    assert q2.question == "Which language is used for web styling?"
    assert q2.correct_option == 2
    assert q2.correct_letter == "C"
    assert q2.explanation == "CSS stands for Cascading Style Sheets."


def test_missing_option(parser: BulkQuizParser):
    """Test parser detecting a question with only 1 option."""
    text = """
Q1. What is Python?
A) A programming language
Answer: A
"""
    result = parser.parse(text)
    assert result.has_errors
    assert result.error_count == 1
    assert "at least 2 options" in result.errors[0].message


def test_invalid_answer(parser: BulkQuizParser):
    """Test parser detecting an answer that does not exist in options."""
    text = """
Q1. What is Python?
A) Language
B) Snake
C) Database
Answer: D
"""
    result = parser.parse(text)
    assert result.has_errors
    assert result.error_count == 1
    err = result.errors[0]
    assert "does not match" in err.message or "does not exist" in err.message
    assert "A, B, C" in err.details


def test_hindi_text(parser: BulkQuizParser):
    """Test parsing Hindi Devanagari text, options, and answers."""
    text = """
प्रश्न 1. भारत की राजधानी क्या है?
A) मुंबई
B) नई दिल्ली
C) कोलकाता
D) चेन्नई
उत्तर: B
व्याख्या: नई दिल्ली भारत की आधिकारिक राजधानी है।
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 1
    q = result.questions[0]
    assert "भारत की राजधानी" in q.question
    assert q.options[1] == "नई दिल्ली"
    assert q.correct_option == 1
    assert q.explanation == "नई दिल्ली भारत की आधिकारिक राजधानी है।"


def test_extra_spaces(parser: BulkQuizParser):
    """Test parser resilience against messy whitespace and formatting."""
    text = """
   
  Q1   What is 2 + 2?   
  
    A.   3  
    B.   4  
    C.   5  
    
  Ans:    B   
  
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 1
    q = result.questions[0]
    assert q.question == "What is 2 + 2?"
    assert q.options == ["3", "4", "5"]
    assert q.correct_option == 1


def test_alternative_question_formats(parser: BulkQuizParser):
    """Test Question 1:, 1., and brackets in options."""
    text = """
Question 1: Who discovered gravity?
(A) Albert Einstein
(B) Isaac Newton
(C) Nikola Tesla
Ans - B

2. Which planet is closest to the Sun?
1) Venus
2) Mercury
3) Mars
Correct Answer: 2
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 2
    assert result.questions[0].correct_letter == "B"
    assert result.questions[0].options[1] == "Isaac Newton"
    # Q2: Mercury is option 2 (index 1)
    assert result.questions[1].correct_option == 1


def test_answer_matching_option_text(parser: BulkQuizParser):
    """Test answer written as full option text (e.g. Answer: New Delhi)."""
    text = """
Q1. What is the capital of India?
A) Mumbai
B) New Delhi
C) Kolkata
D) Chennai
Answer: New Delhi
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 1
    assert result.questions[0].correct_option == 1


def test_empty_input(parser: BulkQuizParser):
    """Test empty and whitespace-only input."""
    assert parser.parse("").is_empty
    assert parser.parse("   \n\t  ").is_empty


def test_emojis_and_symbols(parser: BulkQuizParser):
    """Test parser handling emojis and math symbols."""
    text = """
Q1. What is the value of √16 + 2²? 🧮
A) 6 ❌
B) 8 ✨
C) 10 🚀
Answer: B
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 1
    assert "√16" in result.questions[0].question
    assert result.questions[0].options[1] == "8 ✨"
    assert result.questions[0].correct_option == 1


def test_inline_checkmark_answers_and_bilingual(parser: BulkQuizParser):
    """Test bilingual questions with inline checkmark answer indicators (no Answer: line)."""
    text = """
Q1. How many degrees are there in a compass? कम्पास में कुल कितने डिग्री होते हैं?
A. 360 (360) ✅
B. 270 (270)
C. 180 (180)
D. 90 (90)

Q2. Which direction is indicated by the top of a map? मानचित्र के ऊपरी भाग द्वारा कौन-सी दिशा दर्शाई जाती है?
A. North (उत्तर) ✅
B. West (पश्चिम)
C. South (दक्षिण)
D. East (पूर्व)

Q3. What is used to find North at night? रात में उत्तर दिशा ज्ञात करने के लिए किसका उपयोग किया जाता है?
A. Tree (वृक्ष)
B. Pole Star (ध्रुव तारा) ✅
C. Sun (सूर्य)
D. Moon (चंद्रमा)

Q4. Which type of map provides detailed information about natural features like mountains, rivers, and forests? पर्वतों, नदियों और वनों जैसी प्राकृतिक विशेषताओं की विस्तृत जानकारी कौन-सा मानचित्र देता है?
A. Topographic Map (स्थलाकृतिक मानचित्र) ✅
B. Political Map (राजनीतिक मानचित्र)
C. Road Map (सड़क मानचित्र)
D. Weather Map (मौसम मानचित्र)

Q5. Whose orders will the Section Commander wait for after reorganization in Section Battle Drill? सेक्शन बैटल ड्रिल में पुनर्गठन के बाद सेक्शन कमांडर किसके आदेश की प्रतीक्षा करेगा?
A. Commanding Officer (कमांडिंग ऑफिसर)
B. Company Commander (कंपनी कमांडर)
C. Platoon Commander (प्लाटून कमांडर) ✅
D. None of the above (उपरोक्त में से कोई नहीं)
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 5
    assert result.total_blocks_found == 5

    # Q1: A is correct, option text stripped of ✅, Hindi on new line
    q1 = result.questions[0]
    assert q1.correct_option == 0
    assert q1.correct_letter == "A"
    assert q1.options[0] == "360 (360)"
    assert "✅" not in q1.options[0]
    assert q1.question == "How many degrees are there in a compass?\nकम्पास में कुल कितने डिग्री होते हैं?"

    # Q2: A is correct, Hindi on new line
    q2 = result.questions[1]
    assert q2.correct_option == 0
    assert q2.correct_letter == "A"
    assert q2.options[0] == "North (उत्तर)"
    assert "✅" not in q2.options[0]
    assert q2.question == "Which direction is indicated by the top of a map?\nमानचित्र के ऊपरी भाग द्वारा कौन-सी दिशा दर्शाई जाती है?"


    # Q3: B is correct
    q3 = result.questions[2]
    assert q3.correct_option == 1
    assert q3.correct_letter == "B"
    assert q3.options[1] == "Pole Star (ध्रुव तारा)"
    assert "✅" not in q3.options[1]

    # Q4: A is correct
    q4 = result.questions[3]
    assert q4.correct_option == 0
    assert q4.correct_letter == "A"
    assert q4.options[0] == "Topographic Map (स्थलाकृतिक मानचित्र)"

    # Q5: C is correct
    q5 = result.questions[4]
    assert q5.correct_option == 2
    assert q5.correct_letter == "C"
    assert q5.options[2] == "Platoon Commander (प्लाटून कमांडर)"


def test_format_bilingual_question_text():
    """Verify bilingual formatter splits Hindi to next line."""
    from app.utils.helpers import format_bilingual_question_text

    text = "What is the capital of India? भारत की राजधानी क्या है?"
    formatted = format_bilingual_question_text(text)
    assert formatted == "What is the capital of India?\nभारत की राजधानी क्या है?"

    # Hindi first
    text2 = "भारत की राजधानी क्या है? What is the capital of India?"
    formatted2 = format_bilingual_question_text(text2)
    assert formatted2 == "भारत की राजधानी क्या है?\nWhat is the capital of India?"

    # Pure English remains single line
    assert format_bilingual_question_text("What is Python?") == "What is Python?"

    # Pure Hindi remains single line
    assert format_bilingual_question_text("भारत की राजधानी क्या है?") == "भारत की राजधानी क्या है?"

    # Hinglish / Latin script upcoming words after ?
    text3 = "What is photosynthesis? prakash sanshleshan kya hai?"
    assert format_bilingual_question_text(text3) == "What is photosynthesis?\nprakash sanshleshan kya hai?"

    # Accidental space before ?
    text4 = "What is the capital of India ? भारत की राजधानी क्या है?"
    assert format_bilingual_question_text(text4) == "What is the capital of India?\nभारत की राजधानी क्या है?"

    # Idempotent (already on multiple lines)
    text5 = "What is Python?\nयह क्या है?"
    assert format_bilingual_question_text(text5) == "What is Python?\nयह क्या है?"

    # Acronyms and technical numbers in Hindi part must remain together on next line
    ncc_text = "What is the motto of NCC? NCC का आदर्श वाक्य क्या है?"
    assert (
        format_bilingual_question_text(ncc_text)
        == "What is the motto of NCC?\nNCC का आदर्श वाक्य क्या है?"
    )

    rifle_text = "What is the magazine capacity of a .22 Rifle? .22 राइफल की मैगजीन की क्षमता कितनी होती है?"
    assert (
        format_bilingual_question_text(rifle_text)
        == "What is the magazine capacity of a .22 Rifle?\n.22 राइफल की मैगजीन की क्षमता कितनी होती है?"
    )


def test_iss_5407_bilingual_splitter_safety():
    """Verify ISS-5407: Bilingual question splitter does not incorrectly split English questions with quotes or multiple question marks."""
    from app.utils.helpers import format_bilingual_question_text

    # A. Standard bilingual question
    assert (
        format_bilingual_question_text("What is your name? आपका नाम क्या है?")
        == "What is your name?\nआपका नाम क्या है?"
    )

    # B. English quoted question must NOT be split
    quoted = 'What did he ask when he said "Where are you?" yesterday?'
    assert format_bilingual_question_text(quoted) == quoted

    # Single-quoted question inside English
    single_quoted = "Did the teacher ask 'Why is the sky blue?' during class?"
    assert format_bilingual_question_text(single_quoted) == single_quoted

    # C. English question with multiple question marks must NOT be split
    multi_q = 'Do you know what "Why?" means? Is it a question?'
    assert format_bilingual_question_text(multi_q) == multi_q

    clauses_q = "Is this Option A? Or is this Option B?"
    assert format_bilingual_question_text(clauses_q) == clauses_q

    # D. Hindi question following English with Hindi punctuation
    assert (
        format_bilingual_question_text("Where is the Taj Mahal located? ताजमहल कहाँ स्थित है।")
        == "Where is the Taj Mahal located?\nताजमहल कहाँ स्थित है।"
    )

    # E. Mixed-language question: English sentence with quoted Hindi term
    mixed_quoted = 'What is the historical significance of "सत्याग्रह" in Indian freedom struggle?'
    assert format_bilingual_question_text(mixed_quoted) == mixed_quoted

    # F. No Hindi content: English-only questions ending in ?
    assert format_bilingual_question_text("What is the speed of light?") == "What is the speed of light?"
    assert format_bilingual_question_text("Did you see that?") == "Did you see that?"

    # G. Whitespace variations and multiline formatting
    spaced = "What is your name?    आपका नाम क्या है?"
    assert format_bilingual_question_text(spaced) == "What is your name?\nआपका नाम क्या है?"



def test_duplicate_question_numbers_preserved(parser: BulkQuizParser):
    """Test ISS-5401: Duplicate question numbers across sections are preserved, never overwritten."""
    text = """
1. What is the first question of section 1?
A) Option A1
B) Option B1
Answer: A

2. What is the second question of section 1?
A) Option A2
B) Option B2
Answer: B

1. What is the first question of section 2?
A) Option A3
B) Option B3
Answer: A

2. What is the second question of section 2?
A) Option A4
B) Option B4
Answer: B
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.total_blocks_found == 4
    assert result.valid_count == 4
    assert [q.question for q in result.questions] == [
        "What is the first question of section 1?",
        "What is the second question of section 1?",
        "What is the first question of section 2?",
        "What is the second question of section 2?",
    ]


def test_numeric_question_parenthesis_delimiter(parser: BulkQuizParser):
    """Test ISS-5404: Numeric question numbering with closing parenthesis '1)'."""
    text = """
1) What is the capital of India?
A) Mumbai
B) New Delhi
Answer: B

2) What is the capital of France?
A) Paris
B) London
Answer: A
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 2
    assert result.questions[0].question == "What is the capital of India?"
    assert result.questions[0].correct_option == 1
    assert result.questions[1].question == "What is the capital of France?"
    assert result.questions[1].correct_option == 0


def test_numeric_options_not_confused_with_question_headers(parser: BulkQuizParser):
    """Test ISS-5404: 3) and 4) numeric options are not mistaken for new questions."""
    text = """
1. Which of the following are inner planets?
1) Mercury
2) Venus
3) Earth
4) Mars
Answer: 3

2. Which planet has rings?
1) Mercury
2) Saturn
Answer: 2
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 2
    q1 = result.questions[0]
    assert len(q1.options) == 4
    assert q1.options == ["Mercury", "Venus", "Earth", "Mars"]
    assert q1.correct_option == 2  # Option 3 (index 2)
    assert q1.question == "Which of the following are inner planets?"

    q2 = result.questions[1]
    assert len(q2.options) == 2
    assert q2.options == ["Mercury", "Saturn"]
    assert q2.correct_option == 1


def test_hindi_devanagari_options_and_answers(parser: BulkQuizParser):
    """Test ISS-5403: Hindi Devanagari option labels (क, ख, ग, घ), parenthesized, dotted, and answers."""
    # Standard क) format
    text1 = """
प्रश्न 1. भारत का राष्ट्रीय पशु कौन सा है?
क) शेर
ख) बाघ
ग) हाथी
घ) चीता
उत्तर: ख
"""
    res1 = parser.parse(text1)
    assert not res1.has_errors
    assert res1.valid_count == 1
    q1 = res1.questions[0]
    assert len(q1.options) == 4
    assert q1.options == ["शेर", "बाघ", "हाथी", "चीता"]
    assert q1.correct_option == 1

    # Parenthesized (क) and bare उत्तर ख without colon
    text2 = """
1. सौरमंडल का सबसे बड़ा ग्रह कौन सा है?
(क) मंगल
(ख) बुध
(ग) बृहस्पति
(घ) शुक्र
उत्तर ग
"""
    res2 = parser.parse(text2)
    assert not res2.has_errors
    assert res2.valid_count == 1
    q2 = res2.questions[0]
    assert q2.options[2] == "बृहस्पति"
    assert q2.correct_option == 2

    # Dot format क. and answer with option text
    text3 = """
1. भारत की राजधानी क्या है?
क. मुंबई
ख. नई दिल्ली
ग. कोलकाता
घ. चेन्नई
उत्तर: ख) नई दिल्ली
"""
    res3 = parser.parse(text3)
    assert not res3.has_errors
    assert res3.valid_count == 1
    assert res3.questions[0].correct_option == 1

    # Cross-matching: Hindi options with English Answer: B
    text4 = """
1. टेस्ट प्रश्न?
क) विकल्प 1
ख) विकल्प 2
Answer: B
"""
    res4 = parser.parse(text4)
    assert not res4.has_errors
    assert res4.valid_count == 1
    assert res4.questions[0].correct_option == 1

    # Cross-matching: English options with Hindi उत्तर: ख
    text5 = """
1. Test question?
A) Opt 1
B) Opt 2
उत्तर: ख
"""
    res5 = parser.parse(text5)
    assert not res5.has_errors
    assert res5.valid_count == 1
    assert res5.questions[0].correct_option == 1


def test_roman_numeral_options(parser: BulkQuizParser):
    """Test ISS-5403: Roman numeral option labels (i, ii, iii, iv)."""
    text = """
1. Which layer of the atmosphere is closest to Earth?
(i) Stratosphere
(ii) Troposphere
(iii) Mesosphere
(iv) Thermosphere
Answer: ii
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.valid_count == 1
    q = result.questions[0]
    assert len(q.options) == 4
    assert q.options[1] == "Troposphere"
    assert q.correct_option == 1


def test_correct_answer_prefix_matching_bug_fixed(parser: BulkQuizParser):
    """Test ISS-5402: Verify answers starting with A/B/C/D do not falsely match Option A."""
    # Scenario 1: Answer: Australia when Australia is Option C -> must resolve to C, NOT A
    text1 = """
1. What is the smallest continent by land area?
A) Paris
B) London
C) Australia
D) Tokyo
Answer: Australia
"""
    res1 = parser.parse(text1)
    assert not res1.has_errors
    assert res1.valid_count == 1
    assert res1.questions[0].correct_option == 2  # Option C, NOT Option A!

    # Scenario 2: Answer: All of the above when it is Option D -> must resolve to D, NOT A
    text2 = """
1. Which of the following are states of matter?
A) Solid
B) Liquid
C) Gas
D) All of the above
Answer: All of the above
"""
    res2 = parser.parse(text2)
    assert not res2.has_errors
    assert res2.valid_count == 1
    assert res2.questions[0].correct_option == 3  # Option D, NOT Option A!

    # Scenario 3: Answer: Australia when Australia is NOT an option -> must fail, NEVER match A
    text3 = """
1. What is the capital of France?
A) Paris
B) London
C) Berlin
D) Rome
Answer: Australia
"""
    res3 = parser.parse(text3)
    assert res3.has_errors
    assert res3.error_count == 1
    assert "does not match any available option" in res3.errors[0].message

    # Scenario 4: Delimited key with option text (Answer: B) New Delhi)
    text4 = """
1. What is the capital of India?
A) Mumbai
B) New Delhi
C) Kolkata
D) Chennai
Answer: B) New Delhi
"""
    res4 = parser.parse(text4)
    assert not res4.has_errors
    assert res4.valid_count == 1
    assert res4.questions[0].correct_option == 1

    # Scenario 5: Option prefix (Answer: Option B)
    text5 = """
1. What is 2 + 2?
A) 3
B) 4
C) 5
Answer: Option B
"""
    res5 = parser.parse(text5)
    assert not res5.has_errors
    assert res5.valid_count == 1
    assert res5.questions[0].correct_option == 1


def test_numeric_options_with_question_marks_english(parser: BulkQuizParser):
    """Test Phase 5.4.1.2: English numeric options ending in '?' remain options, not new questions."""
    text = """
1) Which is a valid question?
1) Who are you?
2) Where are you?
3) Why are you?
4) How are you?
Answer: 3
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.total_blocks_found == 1
    assert result.valid_count == 1
    q = result.questions[0]
    assert len(q.options) == 4
    assert q.options == ["Who are you?", "Where are you?", "Why are you?", "How are you?"]
    assert q.correct_option == 2  # Option 3 is 0-based index 2


def test_numeric_options_with_question_marks_hindi(parser: BulkQuizParser):
    """Test Phase 5.4.1.2: Hindi numeric options ending in '?' remain options, not new questions."""
    text = """
1) सही प्रश्न चुनिए?
1) कौन आया?
2) कहाँ गए?
3) क्यों आए?
4) कैसे हुआ?
उत्तर: 2
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.total_blocks_found == 1
    assert result.valid_count == 1
    q = result.questions[0]
    assert len(q.options) == 4
    assert q.options == ["कौन आया?", "कहाँ गए?", "क्यों आए?", "कैसे हुआ?"]
    assert q.correct_option == 1  # Option 2 is 0-based index 1


def test_numeric_options_mixed_punctuation(parser: BulkQuizParser):
    """Test Phase 5.4.1.2: Numeric options ending with '.', ';', ':', '!', '?', and '؟' remain options."""
    text = """
1) Mixed punctuation test?
1) First statement.
2) Second statement;
3) Third statement:
4) Fourth statement!
5) Fifth question?
6) Sixth question؟
Answer: 5
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.total_blocks_found == 1
    assert result.valid_count == 1
    q = result.questions[0]
    assert len(q.options) == 6
    assert q.options == [
        "First statement.",
        "Second statement;",
        "Third statement:",
        "Fourth statement!",
        "Fifth question?",
        "Sixth question؟",
    ]
    assert q.correct_option == 4  # Option 5 is 0-based index 4


def test_consecutive_questions_with_question_mark_options(parser: BulkQuizParser):
    """Test Phase 5.4.1.2: Two consecutive questions with numeric options containing question marks."""
    text = """
1) First question?
1) Who?
2) Where?
3) Why?
Answer: 1

2) Second question?
1) What?
2) When?
3) How?
Answer: 2
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.total_blocks_found == 2
    assert result.valid_count == 2
    q1 = result.questions[0]
    assert q1.question == "First question?"
    assert len(q1.options) == 3
    assert q1.options == ["Who?", "Where?", "Why?"]
    assert q1.correct_option == 0

    q2 = result.questions[1]
    assert q2.question == "Second question?"
    assert len(q2.options) == 3
    assert q2.options == ["What?", "When?", "How?"]
    assert q2.correct_option == 1


def test_numbering_restart_with_numeric_options(parser: BulkQuizParser):
    """Test Phase 5.4.1.2: Numbering restart at 1) after completed question with numeric options."""
    text = """
1) Section 1 Q1?
1) Option 1-1?
2) Option 1-2?
3) Option 1-3?
Answer: 1

1) Section 2 Q1?
1) Option 2-1?
2) Option 2-2?
3) Option 2-3?
Answer: 2
"""
    result = parser.parse(text)
    assert not result.has_errors
    assert result.total_blocks_found == 2
    assert result.valid_count == 2
    assert result.questions[0].question == "Section 1 Q1?"
    assert len(result.questions[0].options) == 3
    assert result.questions[0].correct_option == 0
    assert result.questions[1].question == "Section 2 Q1?"
    assert len(result.questions[1].options) == 3
    assert result.questions[1].correct_option == 1

