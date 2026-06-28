"""Phase 1 迁移测试：_ensure_columns 给存量 connectors 表幂等补列、scan_entries 由 create_all 建出。

无 alembic，新增到既有表的列靠 app.database._ensure_columns 在 init_db 里补。
这里造一个「旧 schema」（不含 first_scan_done / history_boundary_external_id）的临时 sqlite 文件，
跑 _ensure_columns，断言列被加上、幂等（跑两次不报错）、且既有数据无损。
"""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.database import Base, _ensure_columns


def _make_old_schema_db(path: str):
    """建一个只含旧 connectors 列、且没有 scan_entries 的库，模拟上线前的存量库。"""
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE connectors (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    platform VARCHAR(80) NOT NULL,
                    scan_mode VARCHAR(120) NOT NULL DEFAULT 'x',
                    max_scan_pages INTEGER NOT NULL DEFAULT 20
                )
                """
            )
        )
        conn.execute(
            text("INSERT INTO connectors (id, name, platform) VALUES (1, '抖音收藏夹', 'douyin')")
        )
    return eng


def test_ensure_columns_adds_missing_connector_columns(tmp_path):
    db_path = tmp_path / "old.db"
    eng = _make_old_schema_db(str(db_path))

    before = {c["name"] for c in inspect(eng).get_columns("connectors")}
    assert "first_scan_done" not in before
    assert "history_boundary_external_id" not in before
    assert "history_saved" not in before

    _ensure_columns(eng)

    after = {c["name"] for c in inspect(eng).get_columns("connectors")}
    assert "first_scan_done" in after
    assert "history_boundary_external_id" in after
    assert "history_saved" in after

    # 既有行无损，新列取默认值
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT name, first_scan_done, history_boundary_external_id, history_saved "
                "FROM connectors WHERE id=1"
            )
        ).fetchone()
    assert row[0] == "抖音收藏夹"
    assert row[1] == 0  # BOOLEAN DEFAULT 0
    assert row[2] is None
    assert row[3] == 0  # history_saved BOOLEAN DEFAULT 0


def test_ensure_columns_is_idempotent(tmp_path):
    db_path = tmp_path / "old.db"
    eng = _make_old_schema_db(str(db_path))

    _ensure_columns(eng)
    # 第二次必须不报错（列已存在则跳过）
    _ensure_columns(eng)

    after = {c["name"] for c in inspect(eng).get_columns("connectors")}
    assert "first_scan_done" in after
    assert "history_boundary_external_id" in after


def test_ensure_columns_noop_when_table_absent(tmp_path):
    # 没有 connectors 表时（极端情况）不应报错
    db_path = tmp_path / "empty.db"
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    _ensure_columns(eng)  # 不抛异常即可
    assert not inspect(eng).has_table("connectors")


def test_create_all_builds_scan_entries(tmp_path):
    # scan_entries 是新表，靠 create_all 自动建，不进 _ensure_columns
    db_path = tmp_path / "fresh.db"
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=eng)
    _ensure_columns(eng)

    insp = inspect(eng)
    assert insp.has_table("scan_entries")
    cols = {c["name"] for c in insp.get_columns("scan_entries")}
    assert {"platform", "external_item_id", "collection_kind", "usefulness", "published_at", "extracted"} <= cols
