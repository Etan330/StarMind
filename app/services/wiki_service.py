from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import LOCAL_DATA_DIR
from app.llm import get_provider_runtime
from app.models import RawSource, WikiLog, WikiPage


class WikiMaintenanceService:
    SUPPORTED_PAGE_TYPES = {"knowledge", "methodology", "sop", "skill"}

    def __init__(self, db: Session) -> None:
        self.db = db

    async def create_page_from_raw_source(self, raw_source_id: int, page_type: str = "knowledge", force: bool = False) -> WikiPage:
        raw_source = self.db.get(RawSource, raw_source_id)
        if raw_source is None:
            raise ValueError(f"RawSource {raw_source_id} not found")
        page_type = page_type if page_type in self.SUPPORTED_PAGE_TYPES else "knowledge"
        existing = self._find_page_for_raw_source(raw_source.id, page_type)
        transcript = self._read_text(raw_source.transcript_path) or self._read_text(raw_source.clean_text_path)
        markdown = await self._summarize(raw_source, transcript, page_type)
        if existing and not force:
            self._refresh_existing_page_title(existing, self._page_title(raw_source.title, page_type))
            return existing
        if existing and force:
            title = self._page_title(raw_source.title, page_type)
            path = Path(existing.markdown_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            existing.title = title
            existing.tags_json = json.dumps([raw_source.platform, self._page_type_label(page_type)], ensure_ascii=False)
            existing.updated_by = "agent"
            existing.status = "needs_review"
            self._write_log("update_page", existing, raw_source, f"Regenerated {page_type} draft from Raw Source {raw_source.id}.")
            self.db.commit()
            self.db.refresh(existing)
            return existing

        page_id = f"page-{uuid4().hex}"
        path = LOCAL_DATA_DIR / "wiki" / f"{page_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        page = WikiPage(
            page_id=page_id,
            page_type=page_type,
            title=self._page_title(raw_source.title, page_type),
            markdown_path=str(path),
            source_refs_json=json.dumps([{"raw_source_id": raw_source.id, "url": raw_source.canonical_url}], ensure_ascii=False),
            tags_json=json.dumps([raw_source.platform, self._page_type_label(page_type)], ensure_ascii=False),
            status="needs_review",
            updated_by="agent",
        )
        self.db.add(page)
        self.db.flush()
        self._write_log("create_page", page, raw_source, f"Created {page_type} draft from Raw Source {raw_source.id}.")
        self.db.commit()
        self.db.refresh(page)
        return page

    async def _summarize(self, raw_source: RawSource, transcript: str, page_type: str) -> str:
        prompt_map = {
            "knowledge": (
                "请把下面原始资料沉淀成知识库页面。不要照搬原文，要提炼成可复用知识。"
                "结构必须包括：核心定义、关键观点、适用边界、可复用结论、引用来源。"
            ),
            "methodology": (
                "请把下面原始资料提炼成方法论。重点是判断框架和思考原则，不要写成操作清单。"
                "结构必须包括：适用场景、核心原则、判断流程、反例/风险边界、如何复用、引用来源。"
            ),
            "sop": (
                "请把下面原始资料整理成 SOP。重点是可执行步骤，不要写成泛泛总结。"
                "结构必须包括：目标、前置条件、执行步骤、检查清单、异常处理、完成标准、引用来源。"
            ),
            "skill": (
                "请把下面原始资料沉淀成给 iDA Agent 调用的 Skill 说明。重点是让 Agent 知道何时调用、"
                "需要什么输入、怎么执行、如何判断是否成功。结构必须包括：Skill 名称、调用场景、"
                "输入要求、执行步骤、工具/依赖、输出格式、失败处理、可调用性评估（0-100 分，并说明是否建议启用）、引用来源。"
            ),
        }
        prompt = (
            prompt_map.get(page_type, prompt_map["knowledge"])
            + "如果逐字稿不足，请明确标注待补全。\n\n"
            + f"标题：{raw_source.title}\n链接：{raw_source.canonical_url}\n\n逐字稿：\n{transcript[:8000]}"
        )
        try:
            provider, model, _config = get_provider_runtime()
            body = await provider.chat(
                [
                    {"role": "system", "content": "你是 StarMind 的知识库维护 Agent。"},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=0.2,
            )
            if not body.strip():
                raise ValueError("empty model response")
            status_line = "> 生成状态：已调用模型完成沉淀。"
        except Exception:
            body = self._fallback_body(raw_source, transcript, page_type)
            status_line = "> 生成状态：模型未成功返回，当前使用本地兜底模板；配置可用模型后可重新生成。"
        return f"# {self._page_title(raw_source.title, page_type)}\n\n{status_line}\n\n{body}\n\n---\n引用来源：{raw_source.canonical_url}\n"

    def _fallback_body(self, raw_source: RawSource, transcript: str, page_type: str) -> str:
        excerpt = (transcript or "当前还没有拿到逐字稿或正文。")[:3000]
        source = f"- 标题：{raw_source.title}\n- 来源：{raw_source.platform}\n- 链接：{raw_source.canonical_url}"
        fallback_map = {
            "knowledge": (
                "## 核心定义\n\n"
                "模型暂未成功生成，本页先保留可见文本，并标记为待重新沉淀。\n\n"
                "## 关键观点\n\n"
                "- 需要重新调用模型，从原始资料中抽取稳定结论。\n"
                "- 如果这是视频内容，需要补齐 ASR 逐字稿后再沉淀。\n\n"
                f"## 来源信息\n\n{source}\n\n"
                f"## 原始片段\n\n{excerpt}\n"
            ),
            "methodology": (
                "## 适用场景\n\n"
                "模型暂未成功生成，暂时无法判断这条资料能沉淀成哪类方法论。\n\n"
                "## 待提炼的方法论问题\n\n"
                "- 这条资料解决什么问题？\n"
                "- 它背后的判断原则是什么？\n"
                "- 哪些场景适合复用，哪些场景不适合？\n\n"
                "## 风险边界\n\n"
                "- 需要模型重新总结，避免把原文直接当成方法论。\n"
                "- 需要补齐逐字稿后再抽象成判断框架。\n\n"
                f"## 原始片段\n\n{excerpt}\n"
            ),
            "sop": (
                "## 目标\n\n"
                "模型暂未成功生成，当前还不能形成可靠 SOP。\n\n"
                "## 待生成步骤\n\n"
                "1. 补齐逐字稿或正文。\n"
                "2. 识别资料中的动作、顺序、输入和输出。\n"
                "3. 生成可执行步骤和检查清单。\n"
                "4. 标注异常处理和完成标准。\n\n"
                "## 检查清单\n\n"
                "- 每一步是否能被用户或 Agent 执行？\n"
                "- 是否有明确输入和输出？\n"
                "- 是否有失败处理？\n\n"
                f"## 原始片段\n\n{excerpt}\n"
            ),
            "skill": (
                "## Skill 名称\n\n"
                f"{raw_source.title} Skill 草稿\n\n"
                "## 调用场景\n\n"
                "模型暂未成功生成，暂不能建议 iDA Agent 自动启用。\n\n"
                "## 输入要求\n\n"
                "- 待用户或模型补齐：任务目标、上下文、必要资料。\n\n"
                "## 执行步骤\n\n"
                "1. 读取原始资料。\n"
                "2. 识别可复用动作。\n"
                "3. 归纳为 Agent 可执行流程。\n"
                "4. 输出调用条件和失败处理。\n\n"
                "## 可调用性评估\n\n"
                "- 分数：30 / 100\n"
                "- 结论：暂不建议启用。原因是模型未完成总结，逐字稿可能不足。\n\n"
                f"## 原始片段\n\n{excerpt}\n"
            ),
        }
        return fallback_map.get(page_type, fallback_map["knowledge"])

    def _find_page_for_raw_source(self, raw_source_id: int, page_type: str | None = None) -> WikiPage | None:
        for page in self.db.query(WikiPage).all():
            if page_type and page.page_type != page_type:
                continue
            try:
                refs = json.loads(page.source_refs_json or "[]")
            except json.JSONDecodeError:
                refs = []
            if any(int(ref.get("raw_source_id") or 0) == raw_source_id for ref in refs if isinstance(ref, dict)):
                return page
        return None

    def _page_title(self, title: str, page_type: str) -> str:
        if page_type == "skill" and not title.startswith("Skill："):
            return f"Skill：{title}"
        if page_type == "sop" and not title.startswith("SOP："):
            return f"SOP：{title}"
        if page_type == "methodology" and not title.startswith("方法论："):
            return f"方法论：{title}"
        return title

    def _page_type_label(self, page_type: str) -> str:
        return {"knowledge": "知识页面", "methodology": "方法论", "sop": "SOP", "skill": "Skill"}.get(page_type, "知识页面")

    def _refresh_existing_page_title(self, page: WikiPage, title: str) -> None:
        if page.title == title:
            return
        page.title = title
        path = Path(page.markdown_path)
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].startswith("# "):
                lines[0] = f"# {title}"
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.db.commit()

    def _write_log(self, operation: str, page: WikiPage, raw_source: RawSource, summary: str) -> None:
        self.db.add(
            WikiLog(
                operation=operation,
                target_type="wiki_page",
                target_id=page.page_id,
                raw_source_id=raw_source.id,
                affected_pages_json=json.dumps([{"page_id": page.page_id, "page_type": page.page_type}], ensure_ascii=False),
                summary=summary,
            )
        )

    def _read_text(self, path_value: str | None) -> str:
        if not path_value:
            return ""
        path = Path(path_value)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
