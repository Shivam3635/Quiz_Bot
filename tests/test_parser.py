"""Comprehensive tests for BulkQuiz parser."""

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

