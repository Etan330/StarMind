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
    _add_col("push_history", "feedback_requested", "BOOLEAN NOT NULL DEFAULT 0")
    _add_col("recycle_bin_items", "item_type", "VARCHAR(40) NOT NULL DEFAULT 'raw_source'")
    _add_col("recycle_bin_items", "page_id", "VARCHAR(200)")
    _add_col("recycle_bin_items", "raw_source_snapshot_json", "TEXT")
    _add_col("recycle_bin_items", "source_label", "VARCHAR(200)")
    _migrate_knowledge_graph_edges(engine)


def _migrate_knowledge_graph_edges(engine) -> None:
    """Migrate knowledge_graph_edges from raw_source-based to wiki_page-based."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if not inspector.has_table("knowledge_graph_edges"):
        return
    cols = {c["name"] for c in inspector.get_columns("knowledge_graph_edges")}
    # If already migrated to page-based schema, skip
    if "source_page_id" in cols:
        return
    # Drop old table and recreate (old data referenced raw_sources which are stale)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS knowledge_graph_edges"))
    Base.metadata.tables["knowledge_graph_edges"].create(bind=engine, checkfirst=True)
