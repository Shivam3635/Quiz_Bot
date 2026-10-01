"""SQLAlchemy database engine and session management."""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from app.config.settings import get_settings

settings = get_settings()

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

# Connect args needed for SQLite concurrency
connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}

engine = create_engine(
    db_url,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
SessionLocal = scoped_session(SessionFactory)
Base = declarative_base()


def init_db() -> None:
    """Create all tables in the database and ensure column migrations."""
    Base.metadata.create_all(bind=engine)
    if db_url.startswith("sqlite"):
        try:
            with engine.connect() as conn:
                from sqlalchemy import text
                res = conn.execute(text("PRAGMA table_info(quiz_sets)"))
                columns = [row[1] for row in res.fetchall()]
                if "description" not in columns and len(columns) > 0:
                    conn.execute(text("ALTER TABLE quiz_sets ADD COLUMN description TEXT"))
                    conn.commit()
        except Exception:
            pass


def get_db():
    """Context manager / generator for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
