# QuizBotPro Telegram Bot

> **"Create hundreds of Telegram quizzes in seconds."**
> *One paste. One setup. Hundreds of quizzes.*

QuizBotPro is a production-grade Telegram bot designed for quiz channel owners, educators, coaching institutes, and communities. Instead of manually entering dozens of questions, choices, answers, and timer settings one-by-one via traditional bots, QuizBotPro enables users to paste batches of questions, configure common quiz settings once, preview them interactively, and publish native Telegram quiz polls with live progress.

---

## 🚀 Key Features (MVP & Roadmap)

- 📦 **Bulk Question Parsing**: Paste 10, 50, or 100+ questions in clean, natural formats (`Q1. ... A) ... Answer: B`).
- 🌐 **Multilingual & Unicode Support**: Seamlessly parses English, Hindi (Devanagari), Hinglish, symbols, and emojis.
- 🛡️ **Intelligent Validation**: Detects missing options, out-of-bound answer letters, duplicates, and Telegram character limit violations before publishing.
- ⚙️ **One-Time Batch Settings**: Configure anonymity, option shuffling, explanation toggles, timer limits, and target channels in a single pass.
- 👀 **Interactive Pagination Preview**: Preview every question before committing.
- 🚀 **Controlled Publishing & Retries**: Sequential and rate-limited Telegram API publishing with progress feedback and single-click failure retry.
- 🗄️ **Modular Storage**: Built with SQLAlchemy ORM (SQLite for zero-config dev, PostgreSQL-ready for production).

---

## 📁 Architecture Overview

```text
quizbotpro/
├── app/
│   ├── main.py                  # Bot entry point and bootstrap
│   ├── config/                  # Pydantic Settings & environment config
│   ├── bot/                     # Telegram UI handlers, keyboards, and FSM states
│   │   ├── handlers/            # /start, /help, bulk input, settings, preview, publish
│   │   ├── keyboards/           # Inline & Reply keyboards
│   │   └── states.py            # Conversation FSM states
│   ├── parser/                  # Input parsing, models, and rule validators
│   ├── services/                # Business logic: QuizService, PublishingService, TelegramService
│   ├── database/                # SQLAlchemy ORM models, session, and repositories
│   └── utils/                   # Structured logging, error helpers, text utilities
├── tests/                       # Unit & integration test suites
├── .env.example
├── requirements.txt
└── pyproject.toml
```

---

## 🛠️ Setup & Installation

### 1. Prerequisites
- Python 3.12 or higher
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### 2. Clone and Setup Environment

```bash
# Clone the repository
git clone <repo-url>
cd QuizBotPro

# Create and activate virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
DATABASE_URL=sqlite:///quizbotpro.db
LOG_LEVEL=INFO
ENVIRONMENT=development
```

---

## 🧪 Running Tests

Run the full pytest suite:

```bash
pytest -v
```

---

## ▶️ Running the Bot

```bash
python -m app.main
```

---

## 📝 Input Format

```text
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
```
