from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    connector_type: Mapped[str] = mapped_column(String(80), nullable=False, default="mock")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    auth_method: Mapped[str] = mapped_column(String(80), nullable=False, default="none")
    last_successful_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_boundary_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_boundary_external_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_top_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_mode: Mapped[str] = mapped_column(String(120), nullable=False, default="stop_when_seen_existing_raw_source_or_ledger")
    max_scan_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    # 历史/新增收藏夹切分：首次扫描的全部条目算「历史」，记最新一条为分界锚点；之后扫到的算「新增」。
    # 存量库由 app.database._ensure_columns 在 init_db 里幂等补这两列（create_all 不会给已存在的表加列）。
    first_scan_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    history_boundary_external_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # 历史「采集一次保存」：用户保存历史收藏后置 True → 重入历史 Tab 走只读模式（不再扫描/分类）。
    # 「重新扫描历史」会同时清此列与 first_scan_done，使下次扫描重新走 history 全量。
    history_saved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_sync_cron: Mapped[str | None] = mapped_column(String(40), nullable=True, default="0 0 * * *")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    candidates = relationship("CandidateItem", back_populates="connector")
    ledger_items = relationship("SyncLedgerItem", back_populates="connector")


class SyncLedgerItem(Base):
    __tablename__ = "sync_ledger_items"
    __table_args__ = (
        Index("ux_sync_platform_external_item_id", "platform", "external_item_id", unique=True),
        Index("ix_sync_canonical_url", "canonical_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    connector_id: Mapped[int | None] = mapped_column(ForeignKey("connectors.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    external_item_id: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_url: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    scan_run_id: Mapped[str] = mapped_column(String(120), nullable=False)
    classification_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_items.id"), nullable=True)
    raw_source_id: Mapped[int | None] = mapped_column(ForeignKey("raw_sources.id"), nullable=True)
    is_boundary_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    connector = relationship("Connector", back_populates="ledger_items")
    candidate = relationship("CandidateItem", back_populates="ledger_item")


class CandidateItem(Base):
    __tablename__ = "candidate_items"
    __table_args__ = (Index("ix_candidate_canonical_url", "canonical_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False, default="active_connector")
    platform: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    connector_id: Mapped[int | None] = mapped_column(ForeignKey("connectors.id"), nullable=True)
    external_item_id: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="pending_classification")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    connector = relationship("Connector", back_populates="candidates")
    ledger_item = relationship("SyncLedgerItem", back_populates="candidate", uselist=False)


class KnowledgeClassification(Base):
    __tablename__ = "knowledge_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_items.id"), nullable=False, index=True)
    is_knowledge: Mapped[bool] = mapped_column(Boolean, nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    knowledge_type_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decision: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RawSource(Base):
    __tablename__ = "raw_sources"
    __table_args__ = (Index("ix_raw_source_canonical_url", "canonical_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_items.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    external_item_id: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    raw_content_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    clean_text_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    immutable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retention_policy: Mapped[str] = mapped_column(String(160), default="keep_forever_unless_user_deletes", nullable=False)
    agent_delete_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RecycleBinItem(Base):
    __tablename__ = "recycle_bin_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_items.id"), nullable=True)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    external_item_id: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="non_knowledge")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: utcnow() + timedelta(days=30), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="archived")


class WikiPage(Base):
    __tablename__ = "wiki_pages"
    __table_args__ = (Index("ux_wiki_page_id", "page_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    page_id: Mapped[str] = mapped_column(String(200), nullable=False)
    page_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    markdown_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="active")
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")


class WikiLog(Base):
    __tablename__ = "wiki_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_source_id: Mapped[int | None] = mapped_column(ForeignKey("raw_sources.id"), nullable=True)
    affected_pages_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ProductEvent(Base):
    __tablename__ = "product_events"
    __table_args__ = (
        Index("ix_product_event_name", "event_name"),
        Index("ix_product_event_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_name: Mapped[str] = mapped_column(String(120), nullable=False)
    properties_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_items.id"), nullable=True)
    raw_source_id: Mapped[int | None] = mapped_column(ForeignKey("raw_sources.id"), nullable=True)
    page_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(200), nullable=False)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    previous_version_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_version_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ScanLog(Base):
    __tablename__ = "scan_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    connector_id: Mapped[int | None] = mapped_column(ForeignKey("connectors.id"), nullable=True)
    scan_run_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(40), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class OnboardingStatus(Base):
    __tablename__ = "onboarding_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    domain: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    score: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PushSettings(Base):
    __tablename__ = "push_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    start_time: Mapped[str] = mapped_column(String(5), default="08:00", nullable=False)
    end_time: Mapped[str] = mapped_column(String(5), default="22:00", nullable=False)
    frequency_hours: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    items_per_push: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PushHistory(Base):
    __tablename__ = "push_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    raw_source_id: Mapped[int] = mapped_column(ForeignKey("raw_sources.id"), nullable=False)
    pushed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    messages = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    conversation = relationship("ChatConversation", back_populates="messages")


class KnowledgeGraphEdge(Base):
    __tablename__ = "knowledge_graph_edges"
    __table_args__ = (
        Index("ux_graph_edge", "source_id", "target_id", "relation", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("raw_sources.id"), nullable=False)
    target_id: Mapped[int] = mapped_column(ForeignKey("raw_sources.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String(80), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    shared_concepts_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ScanEntry(Base):
    """扫描+分类结果的权威持久层。

    一条收藏跨「扫描展示 → 勾选建 CandidateItem → 提取建 RawSource」三个阶段由这张表串起来，
    与现有 candidate/ledger/raw_source 解耦。DB 为权威源，前端 localStorage 仅作离线缓存/断点续跑。
    唯一约束 (platform, external_item_id) 与 sync_ledger_items 对齐，便于去重对照。
    """

    __tablename__ = "scan_entries"
    __table_args__ = (
        Index("ux_scan_entry_platform_external", "platform", "external_item_id", unique=True),
        Index("ix_scan_entry_kind", "platform", "collection_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    external_item_id: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # history | incremental
    collection_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="incremental")
    # useful | useless | None(未分类)
    usefulness: Mapped[str | None] = mapped_column(String(20), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # DOM 抓到的发布时间原文，抓不到留 None
    published_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    extracted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_items.id"), nullable=True)
    raw_source_id: Mapped[int | None] = mapped_column(ForeignKey("raw_sources.id"), nullable=True)
    scan_run_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
