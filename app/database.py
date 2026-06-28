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


def _ensure_columns(engine) -> None:
    """给存量表幂等补列。

    create_all 只会建新表、不会给已存在的表 ALTER 加列。本项目无 alembic，所以新增到
    既有表（如 connectors）的列需要在这里用 inspector 查、缺失才 ADD COLUMN。
    SQLite 支持带常量 DEFAULT 的 ALTER TABLE ADD COLUMN。内存测试库每次都是全新 create_all，
    列已存在 → 此函数 no-op，不影响测试。新表（如 scan_entries）靠 create_all，不进这里。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    # {table: [(column_name, sqlite_ddl_with_optional_default), ...]}
    required = {
        "connectors": [
            ("first_scan_done", "BOOLEAN NOT NULL DEFAULT 0"),
            ("history_boundary_external_id", "VARCHAR(300)"),
            ("history_saved", "BOOLEAN NOT NULL DEFAULT 0"),
        ],
    }
    for table, cols in required.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        missing = [(name, ddl) for name, ddl in cols if name not in existing]
        if not missing:
            continue
        with engine.begin() as conn:
            for name, ddl in missing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    ensure_directories()
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_columns(engine)

