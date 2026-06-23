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


class SourceConnection(Base):
    __tablename__ = "source_connections"
    __table_args__ = (Index("ix_source_connection_platform_type", "platform", "type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="not_connected")
    auth_status: Mapped[str] = mapped_column(String(80), nullable=False, default="not_configured")
    sync_settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    tasks = relationship("ImportTask", back_populates="source")


class ImportTask(Base):
    __tablename__ = "import_tasks"
    __table_args__ = (
        Index("ix_import_task_status", "status"),
        Index("ix_import_task_type", "type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source_connections.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="queued")
    current_step: Mapped[str] = mapped_column(String(120), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="导入任务")
    provider: Mapped[str] = mapped_column(String(120), nullable=False, default="mock_import_adapter")
    scope_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    input_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discarded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_popup_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    source = relationship("SourceConnection", back_populates="tasks")
    imported_items = relationship("ImportedItem", back_populates="task")
    transcripts = relationship("TranscriptRecord", back_populates="task")
    workflow_logs = relationship("WorkflowRunLog", back_populates="task")


class ImportedItem(Base):
    __tablename__ = "imported_items"
    __table_args__ = (Index("ix_imported_item_task_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("import_tasks.id"), nullable=False)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source_connections.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False, default="video")
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="ready")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    discard_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    task = relationship("ImportTask", back_populates="imported_items")
    transcripts = relationship("TranscriptRecord", back_populates="imported_item")


class TranscriptRecord(Base):
    __tablename__ = "transcript_records"
    __table_args__ = (Index("ix_transcript_imported_item_id", "imported_item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("import_tasks.id"), nullable=False)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source_connections.id"), nullable=True)
    imported_item_id: Mapped[int | None] = mapped_column(ForeignKey("imported_items.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False, default="mock_transcript_adapter")
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="generated")
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    edited_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    logs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    task = relationship("ImportTask", back_populates="transcripts")
    imported_item = relationship("ImportedItem", back_populates="transcripts")


class WorkflowRunLog(Base):
    __tablename__ = "workflow_run_logs"
    __table_args__ = (Index("ix_workflow_log_task_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("import_tasks.id"), nullable=False)
    step: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="completed")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    task = relationship("ImportTask", back_populates="workflow_logs")


class ActivationResult(Base):
    __tablename__ = "activation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    knowledge_page_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False, default="reuse")
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="draft")
    result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
