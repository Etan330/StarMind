from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL, ensure_directories


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    ensure_directories()
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations() -> None:
    """Add missing columns for v2.2 features (safe for SQLite)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    def _add_col(table: str, col: str, col_type: str) -> None:
        if table not in inspector.get_table_names():
            return
        existing = [c["name"] for c in inspector.get_columns(table)]
        if col not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))

    _add_col("raw_sources", "is_distilled", "BOOLEAN NOT NULL DEFAULT 0")
    _add_col("raw_sources", "distilled_at", "DATETIME")
    _add_col("raw_sources", "preliminary_category", "VARCHAR(120)")
    _add_col("wiki_pages", "category_id", "INTEGER REFERENCES wiki_categories(id)")
    _add_col("push_settings", "push_days", "VARCHAR(20) NOT NULL DEFAULT '1,2,3,4,5'")
    _add_col("push_settings", "push_time", "VARCHAR(5) NOT NULL DEFAULT '09:00'")
    _add_col("push_settings", "total_push_count", "INTEGER NOT NULL DEFAULT 0")
    _add_col("push_history", "wiki_page_id", "INTEGER")
    _add_col("push_history", "category_name", "VARCHAR(120)")

