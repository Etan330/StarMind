from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import LOCAL_DATA_DIR
from app.models import ImportedItem, ImportTask, SourceConnection, TranscriptRecord, WikiPage, WorkflowRunLog


class V31WorkflowService:
    """Local V3.1 workflow facade.

    The default implementation is an explicit mock adapter. It creates durable
    product states without claiming real Douyin, Doubao, n8n, or headless-browser
    success.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_source(self, platform: str, source_type: str, name: str | None = None) -> SourceConnection:
        source = (
            self.db.query(SourceConnection)
            .filter(SourceConnection.platform == platform, SourceConnection.type == source_type)
            .first()
        )
        if source:
            return source
        display_name = name or self._source_name(platform, source_type)
        source = SourceConnection(
            platform=platform,
            type=source_type,
            name=display_name,
            status="connected" if platform in {"general_link", "manual_idea"} else "needs_auth",
            auth_status="mock_ready" if platform in {"general_link", "manual_idea"} else "mock_needs_real_authorization",
            sync_settings_json=json.dumps({"scope": "latest_10", "duplicate_handling": "skip"}, ensure_ascii=False),
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        return source

    def create_link_import_task(self, urls: list[str]) -> ImportTask:
        clean_urls = [url.strip() for url in urls if url.strip()]
        if not clean_urls:
            raise ValueError("at least one URL is required")
        source = self.ensure_source("general_link", "link_import", "通用链接")
        task = self._create_task(
            source=source,
            task_type="link_import",
            title=f"导入 {len(clean_urls)} 个链接",
            input_payload={"urls": clean_urls},
            scope={"count": len(clean_urls)},
        )
        for index, url in enumerate(clean_urls, start=1):
            item = self._add_item(
                task,
                title=self._title_for_url(url, index),
                canonical_url=url,
                summary="本地模拟导入的链接内容，等待用户确认后沉淀到知识库。",
                raw_content=f"URL: {url}\n\n这是 V3.1 mock adapter 生成的原始内容占位，用于验证产品闭环。",
                tags=["链接导入", "mock"],
                selected=True,
                score=0.82,
                status="ready",
            )
            self._add_transcript(item, quality=0.82)
        task.imported_count = len(clean_urls)
        task.saved_count = 0
        task.status = "needs_confirmation"
        task.current_step = "needs_confirmation"
        task.progress = 88
        task.result_json = json.dumps(
            {
                "imported": len(clean_urls),
                "ready_for_kb": len(clean_urls),
                "discarded": 0,
                "failed": 0,
                "adapter_notice": "mock_import_adapter: 未接入真实链接抓取或抖音转写。",
            },
            ensure_ascii=False,
        )
        self._log(task, "needs_confirmation", "completed", "链接已完成本地模拟导入，等待确认入库。")
        self.db.commit()
        self.db.refresh(task)
        return task

    def create_favorites_sync_task(self, source_id: int, latest_count: int = 10) -> ImportTask:
        source = self.db.get(SourceConnection, source_id)
        if source is None:
            raise ValueError(f"SourceConnection {source_id} not found")
        count = max(1, min(int(latest_count or 10), 50))
        source.status = "syncing"
        source.auth_status = "mock_needs_real_authorization"
        task = self._create_task(
            source=source,
            task_type="favorites_sync",
            title=f"同步抖音收藏夹最新 {count} 条",
            input_payload={"latest_count": count, "platform": "douyin", "provider": "mock_import_adapter"},
            scope={"latest_count": count},
        )
        for index in range(1, count + 1):
            status = "ready"
            selected = True
            score = 0.78
            discard_reason = None
            if index in {7, 8, 9}:
                status = "discarded"
                selected = False
                score = 0.28
                discard_reason = "后台评分认为这条更像非知识类或低信息密度内容。"
            if index == count:
                status = "failed"
                selected = False
                score = None
                discard_reason = "mock provider 模拟转写失败，需后续接入真实 transcript adapter。"
            item = self._add_item(
                task,
                title=f"抖音收藏内容 {index}",
                canonical_url=f"https://www.douyin.com/video/mock-favorite-{index}",
                summary="来自抖音收藏夹的模拟条目，用于验证最新 10 条同步闭环。",
                raw_content=f"这是第 {index} 条模拟收藏内容。真实同步需要用户授权和平台允许。",
                tags=["抖音收藏", "mock"],
                selected=selected,
                score=score,
                status=status,
                discard_reason=discard_reason,
            )
            if status != "failed":
                self._add_transcript(item, quality=score or 0.65)
        selected_count = self.db.query(ImportedItem).filter(ImportedItem.task_id == task.id, ImportedItem.selected.is_(True)).count()
        task.imported_count = count
        task.saved_count = selected_count
        task.discarded_count = 3 if count >= 9 else 0
        task.failed_count = 1 if count >= 10 else 0
        task.status = "needs_confirmation"
        task.current_step = "needs_confirmation"
        task.progress = 90
        task.external_popup_required = False
        task.result_json = json.dumps(
            {
                "imported": task.imported_count,
                "ready_for_kb": task.saved_count,
                "discarded": task.discarded_count,
                "failed": task.failed_count,
                "adapter_notice": "mock_import_adapter: 未启动浏览器或第三方 App。",
            },
            ensure_ascii=False,
        )
        source.status = "connected"
        source.last_synced_at = datetime.now()
        self._log(task, "scoring", "completed", f"后台知识价值评分完成：{task.saved_count} 条可入库，{task.discarded_count} 条丢弃。")
        self.db.commit()
        self.db.refresh(task)
        return task

    def create_creator_distill_task(
        self,
        creator_name: str,
        profile_url: str,
        latest_count: int = 10,
        topic: str = "",
    ) -> ImportTask:
        source = self.ensure_source("douyin", "creator", f"抖音博主：{creator_name or '未命名博主'}")
        task = self._create_task(
            source=source,
            task_type="creator_distill",
            title=f"蒸馏博主：{creator_name or '未命名博主'}",
            input_payload={
                "creator_name": creator_name,
                "profile_url": profile_url,
                "latest_count": latest_count,
                "topic": topic,
            },
            scope={"latest_count": latest_count, "experimental": True},
        )
        item = self._add_item(
            task,
            title=f"{creator_name or '博主'}：内容方法洞察",
            canonical_url=profile_url or f"starmind://creator/{uuid4().hex}",
            summary="博主蒸馏仍是实验能力。本条是本地模拟洞察，用于验证任务、逐字稿、确认、入库路径。",
            raw_content="模拟内容：选题方式、表达结构、用户互动和可复用洞察。",
            tags=["博主洞察", "实验能力", "mock"],
            selected=True,
            score=0.72,
            status="ready",
        )
        self._add_transcript(item, quality=0.72)
        task.imported_count = 1
        task.saved_count = 0
        task.status = "needs_confirmation"
        task.current_step = "needs_confirmation"
        task.progress = 86
        task.result_json = json.dumps(
            {
                "imported": 1,
                "ready_for_kb": 1,
                "discarded": 0,
                "failed": 0,
                "adapter_notice": "creator mock: 真实博主批量同步尚未接入，结果需用户确认。",
            },
            ensure_ascii=False,
        )
        self._log(task, "creator_mock", "completed", "博主蒸馏实验任务已生成可确认草稿。")
        self.db.commit()
        self.db.refresh(task)
        return task

    def create_idea_task(self, content: str, output_type: str = "knowledge", tags: str = "") -> ImportTask:
        source = self.ensure_source("manual_idea", "idea", "手动 Idea")
        task = self._create_task(
            source=source,
            task_type="idea_capture",
            title="实时记录 Idea",
            input_payload={"content": content, "output_type": output_type, "tags": tags},
            scope={"output_type": output_type},
        )
        item = self._add_item(
            task,
            title=self._idea_title(content, output_type),
            canonical_url=f"starmind://idea/{uuid4().hex}",
            summary="用户输入的 Idea 已整理成可编辑知识草稿。",
            raw_content=content,
            tags=[output_type, *[tag.strip() for tag in tags.split(",") if tag.strip()]],
            selected=True,
            score=0.9,
            status="ready",
            content_type="idea",
        )
        self._add_transcript(item, quality=0.95, status="manual")
        task.imported_count = 1
        task.saved_count = 0
        task.status = "needs_confirmation"
        task.current_step = "needs_confirmation"
        task.progress = 92
        task.result_json = json.dumps({"imported": 1, "ready_for_kb": 1, "discarded": 0, "failed": 0}, ensure_ascii=False)
        self._log(task, "idea_structured", "completed", "Idea 已结构化为知识草稿，等待确认。")
        self.db.commit()
        self.db.refresh(task)
        return task

    def save_task_to_knowledge(self, task_id: int) -> list[WikiPage]:
        task = self.db.get(ImportTask, task_id)
        if task is None:
            raise ValueError(f"ImportTask {task_id} not found")
        items = (
            self.db.query(ImportedItem)
            .filter(ImportedItem.task_id == task.id, ImportedItem.selected.is_(True), ImportedItem.status != "failed")
            .order_by(ImportedItem.created_at.asc())
            .all()
        )
        pages: list[WikiPage] = []
        for item in items:
            existing = self._page_for_item(item.id)
            if existing:
                pages.append(existing)
                continue
            transcript = self._transcript_for_item(item.id)
            page_id = f"page-{uuid4().hex}"
            markdown = self._knowledge_markdown(task, item, transcript)
            path = LOCAL_DATA_DIR / "wiki" / f"{page_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            refs = [
                {
                    "task_id": task.id,
                    "imported_item_id": item.id,
                    "transcript_id": transcript.id if transcript else None,
                    "source_id": item.source_id,
                    "url": item.canonical_url,
                    "provider": task.provider,
                    "mock": task.provider.startswith("mock"),
                }
            ]
            page = WikiPage(
                page_id=page_id,
                page_type="skill" if task.type == "idea_capture" and "skill" in task.input_json.lower() else "knowledge",
                title=item.title,
                markdown_path=str(path),
                source_refs_json=json.dumps(refs, ensure_ascii=False),
                tags_json=item.tags_json,
                status="active",
                updated_by="v3_1_mock_workflow",
            )
            self.db.add(page)
            pages.append(page)
            item.status = "saved_to_kb"
        task.status = "saved_to_kb"
        task.current_step = "saved_to_kb"
        task.progress = 100
        task.saved_count = len(pages)
        self._log(task, "saved_to_kb", "completed", f"{len(pages)} 条内容已保存到知识库。")
        self.db.commit()
        for page in pages:
            self.db.refresh(page)
        return pages

    def _create_task(self, source: SourceConnection, task_type: str, title: str, input_payload: dict[str, Any], scope: dict[str, Any]) -> ImportTask:
        task = ImportTask(
            source_id=source.id,
            type=task_type,
            title=title,
            status="queued",
            current_step="queued",
            provider="mock_import_adapter",
            scope_json=json.dumps(scope, ensure_ascii=False),
            input_json=json.dumps(input_payload, ensure_ascii=False),
            logs_json="[]",
            external_popup_required=False,
        )
        self.db.add(task)
        self.db.flush()
        self._log(task, "queued", "completed", "已创建后台任务。")
        self._log(task, "importing", "completed", "mock adapter 已生成可检查的导入结果。")
        self._log(task, "transcribing", "completed", "mock transcript adapter 已生成 Markdown 逐字稿。")
        self._log(task, "scoring", "completed", "后台知识价值评分已完成。")
        return task

    def _add_item(
        self,
        task: ImportTask,
        title: str,
        canonical_url: str,
        summary: str,
        raw_content: str,
        tags: list[str],
        selected: bool,
        score: float | None,
        status: str,
        discard_reason: str | None = None,
        content_type: str = "video",
    ) -> ImportedItem:
        item = ImportedItem(
            task_id=task.id,
            source_id=task.source_id,
            title=title,
            summary=summary,
            raw_content=raw_content,
            canonical_url=canonical_url,
            author="mock adapter",
            content_type=content_type,
            selected=selected,
            tags_json=json.dumps(tags, ensure_ascii=False),
            status=status,
            score=score,
            discard_reason=discard_reason,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def _add_transcript(self, item: ImportedItem, quality: float, status: str = "generated") -> TranscriptRecord:
        content = (
            f"# 逐字稿：{item.title}\n\n"
            f"来源：{item.canonical_url}\n"
            f"转写来源：mock_transcript_adapter\n"
            f"转写状态：{status}\n"
            f"质量提示：本内容为模拟逐字稿，不能代表真实抖音或豆包转写结果。\n\n"
            "## 正文\n\n"
            f"模拟逐字稿：{item.raw_content}\n\n"
            "这段 Markdown 用于验证导入、转写、评分、确认和入库的产品闭环。"
        )
        transcript = TranscriptRecord(
            task_id=item.task_id,
            source_id=item.source_id,
            imported_item_id=item.id,
            provider="mock_transcript_adapter",
            status=status,
            quality_score=quality,
            content=content,
            editable=True,
            logs_json=json.dumps([{"message": "Generated by explicit mock transcript adapter."}], ensure_ascii=False),
        )
        self.db.add(transcript)
        self.db.flush()
        return transcript

    def _log(self, task: ImportTask, step: str, status: str, message: str) -> None:
        self.db.add(WorkflowRunLog(task_id=task.id, step=step, status=status, message=message))

    def _page_for_item(self, imported_item_id: int) -> WikiPage | None:
        for page in self.db.query(WikiPage).all():
            try:
                refs = json.loads(page.source_refs_json or "[]")
            except json.JSONDecodeError:
                refs = []
            if any(ref.get("imported_item_id") == imported_item_id for ref in refs if isinstance(ref, dict)):
                return page
        return None

    def _transcript_for_item(self, imported_item_id: int) -> TranscriptRecord | None:
        return self.db.query(TranscriptRecord).filter(TranscriptRecord.imported_item_id == imported_item_id).first()

    def _knowledge_markdown(self, task: ImportTask, item: ImportedItem, transcript: TranscriptRecord | None) -> str:
        quality = f"{int((transcript.quality_score or 0) * 100)}%" if transcript else "未知"
        return (
            f"# {item.title}\n\n"
            "> 状态：V3.1 本地 mock workflow 生成，等待接入真实来源和转写服务后可重新生成。\n\n"
            "## 摘要\n\n"
            f"{item.summary}\n\n"
            "## 关键观点\n\n"
            "- 这条内容已经通过后台知识价值评分或用户确认，进入知识库。\n"
            "- 来源、任务和逐字稿引用会保留，便于后续复核。\n"
            "- 当前 provider 为 mock，不代表真实平台同步成功。\n\n"
            "## 来源与逐字稿\n\n"
            f"- 原始链接：{item.canonical_url}\n"
            f"- 导入任务：{task.id}\n"
            f"- 转写质量：{quality}\n"
            f"- 转写记录：{transcript.id if transcript else '无'}\n\n"
            "## 可复用动作\n\n"
            "- 基于此条提问\n"
            "- 生成内容草稿\n"
            "- 生成任务或 Skill 草稿\n"
        )

    def _source_name(self, platform: str, source_type: str) -> str:
        if platform == "douyin" and source_type == "favorites":
            return "抖音收藏夹"
        if platform == "douyin" and source_type == "creator":
            return "抖音博主来源"
        if platform == "general_link":
            return "通用链接"
        if platform == "manual_idea":
            return "手动 Idea"
        return f"{platform} {source_type}"

    def _title_for_url(self, url: str, index: int) -> str:
        if "douyin.com" in url:
            return f"抖音链接内容 {index}"
        return f"导入链接内容 {index}"

    def _idea_title(self, content: str, output_type: str) -> str:
        prefix = {"skill": "Skill 草稿", "sop": "SOP 草稿", "task": "任务方案", "knowledge": "知识笔记"}.get(output_type, "知识草稿")
        compact = " ".join(content.strip().split())
        return f"{prefix}：{compact[:36] or '未命名 Idea'}"
