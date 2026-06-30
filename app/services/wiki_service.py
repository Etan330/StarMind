from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import LOCAL_DATA_DIR
from app.llm import get_provider_runtime
from app.models import KnowledgeGraphEdge, RawSource, WikiLog, WikiPage


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
        await self._build_wiki_edges(page, raw_source)
        return page

    async def _build_wiki_edges(self, new_page: WikiPage, raw_source: RawSource | None = None) -> None:
        """Ask LLM to find relationships between new_page and existing wiki pages, then create KnowledgeGraphEdge."""
        other_pages = [p for p in self.db.query(WikiPage).filter(WikiPage.status.in_(["active", "needs_review"])).all() if p.page_id != new_page.page_id]
        if not other_pages:
            return

        prompt, page_index = self._edge_prompt(new_page, other_pages)

        edges_created = False
        try:
            provider, model, _config = get_provider_runtime()
            body = await provider.chat(
                [
                    {"role": "system", "content": "你是知识关联分析专家。只返回 JSON 数组。"},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=0.2,
            )
            relations = self._parse_edge_response(body, page_index)
            for rel in relations:
                self._create_edge(new_page.page_id, rel["page_id"], rel["relation"], rel.get("reason", ""), rel.get("confidence", 0.7), rel.get("concepts", []))
            edges_created = bool(relations)
        except Exception as e:
            import logging
            logging.getLogger("starmind.wiki").warning(f"LLM edge building failed: {e}")

        if not edges_created:
            return

    async def rebuild_ai_edges_for_pages(self, pages: list[WikiPage] | None = None) -> int:
        pages = pages or self.db.query(WikiPage).filter(WikiPage.status.in_(["active", "needs_review"])).all()
        pages = [p for p in pages if p.status in {"active", "needs_review"}]
        before = self.db.query(KnowledgeGraphEdge).count()
        for page in pages:
            await self._build_wiki_edges(page, None)
        after = self.db.query(KnowledgeGraphEdge).count()
        return after - before

    def _edge_prompt(self, new_page: WikiPage, other_pages: list[WikiPage]) -> tuple[str, list[dict[str, str]]]:
        page_index = []
        for p in other_pages:
            page_index.append({
                "page_id": p.page_id,
                "title": p.title,
                "excerpt": self._read_text(p.markdown_path)[:800],
            })

        markdown_text = self._read_text(new_page.markdown_path)[:1200]
        page_list_text = "\n".join(
            f"- [{item['page_id']}] {item['title']}\n  摘要片段：{item['excerpt']}"
            for item in page_index
        )

        prompt = (
            "【知识关联分析】\n"
            "你正在为 StarMind 知识库建立 Obsidian 风格的双向知识链接。\n"
            "只连接强相关的知识：必须存在明确的概念继承、方法复用、同一问题域、因果/对照关系，不能因为来源平台、泛泛标签或标题相似就连接。\n"
            f"新知识页面：{new_page.title}\n"
            f"新知识正文：{markdown_text}\n\n"
            f"已有知识页面列表：\n{page_list_text}\n\n"
            "请判断新页面与哪些已有页面有真实知识关联，返回 JSON 数组。每项必须包含：\n"
            "page_id、relation、reason、confidence、concepts。\n"
            "relation 可选值：topic_overlap（同一问题域）、extends（延伸扩展）、method_same（方法论相同）、supports（互相支撑）、contradicts（观点对立）。\n"
            "confidence 是 0 到 1 的数字，只返回 confidence >= 0.75 的强相关链接。\n"
            "concepts 是共同概念数组，reason 用一句话说明为什么应该链接。\n"
            "如果没有强相关则返回空数组 []。只返回 JSON，不要其他文字。"
        )
        return prompt, page_index

    def _parse_edge_response(self, body: str, page_index: list[dict]) -> list[dict]:
        text = body.strip()
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return []
        try:
            items = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
        valid_ids = {item["page_id"] for item in page_index}
        result = []
        for item in items:
            pid = item.get("page_id", "")
            confidence = self._normalize_confidence(item.get("confidence"))
            if pid in valid_ids and confidence >= 0.75:
                concepts = item.get("concepts") if isinstance(item.get("concepts"), list) else []
                result.append({
                    "page_id": pid,
                    "relation": item.get("relation", "topic_overlap"),
                    "reason": item.get("reason", ""),
                    "confidence": confidence,
                    "concepts": [str(c) for c in concepts if str(c).strip()],
                })
        return result

    def _normalize_confidence(self, value) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))

    def _create_edge(self, source_page_id: str, target_page_id: str, relation: str, reason: str, confidence: float = 0.7, concepts: list[str] | None = None) -> None:
        if source_page_id == target_page_id:
            return
        existing = self.db.query(KnowledgeGraphEdge).filter(
            KnowledgeGraphEdge.relation == relation,
            (
                (KnowledgeGraphEdge.source_page_id == source_page_id) & (KnowledgeGraphEdge.target_page_id == target_page_id)
            ) | (
                (KnowledgeGraphEdge.source_page_id == target_page_id) & (KnowledgeGraphEdge.target_page_id == source_page_id)
            ),
        ).first()
        if existing:
            return
        shared_concepts = concepts or ([reason] if reason else [])
        self.db.add(KnowledgeGraphEdge(
            source_page_id=source_page_id,
            target_page_id=target_page_id,
            relation=relation,
            weight=confidence,
            shared_concepts_json=json.dumps(shared_concepts, ensure_ascii=False),
        ))
        self.db.commit()

    async def create_creator_page(self, creator_key: str, force: bool = False) -> WikiPage:
        sources = self._creator_sources(creator_key)
        if not sources:
            raise ValueError(f"Creator {creator_key} has no distill_profile sources")
        first = sources[0]
        first_meta = self._json_dict(first.metadata_json)
        creator_name = str(first_meta.get("creator_name") or first.author or "未命名博主").strip()
        existing = self._find_creator_page(creator_key)
        markdown = await self._creator_analysis_markdown(creator_key, creator_name, sources)
        title = f"博主分析：{creator_name}"
        refs = [
            {"raw_source_id": source.id, "url": source.canonical_url, "title": source.title}
            for source in sources
        ]
        tags = ["博主", f"creator:{creator_key}", creator_name, first.platform]

        if existing and not force:
            return existing
        if existing and force:
            path = Path(existing.markdown_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            existing.title = title
            existing.source_refs_json = json.dumps(refs, ensure_ascii=False)
            existing.tags_json = json.dumps(tags, ensure_ascii=False)
            existing.updated_by = "agent"
            existing.status = "needs_review"
            self._write_log("update_creator_page", existing, first, f"Regenerated creator analysis for {creator_name}.")
            self.db.commit()
            self.db.refresh(existing)
            return existing

        page_id = f"creator-{uuid4().hex}"
        path = LOCAL_DATA_DIR / "wiki" / f"{page_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        page = WikiPage(
            page_id=page_id,
            page_type="creator",
            title=title,
            markdown_path=str(path),
            source_refs_json=json.dumps(refs, ensure_ascii=False),
            tags_json=json.dumps(tags, ensure_ascii=False),
            status="needs_review",
            updated_by="agent",
        )
        self.db.add(page)
        self.db.flush()
        self._write_log("create_creator_page", page, first, f"Created creator analysis for {creator_name} from {len(sources)} sources.")
        self.db.commit()
        self.db.refresh(page)
        return page

    def _creator_sources(self, creator_key: str) -> list[RawSource]:
        matches: list[RawSource] = []
        for source in self.db.query(RawSource).filter(RawSource.source_type == "distill_profile").all():
            metadata = self._json_dict(source.metadata_json)
            if str(metadata.get("creator_key") or "") == creator_key:
                matches.append(source)
        return matches

    def _find_creator_page(self, creator_key: str) -> WikiPage | None:
        marker = f"creator:{creator_key}"
        for page in self.db.query(WikiPage).filter(WikiPage.page_type == "creator").all():
            try:
                tags = json.loads(page.tags_json or "[]")
            except json.JSONDecodeError:
                tags = []
            if marker in tags:
                return page
        return None

    async def _creator_analysis_markdown(self, creator_key: str, creator_name: str, sources: list[RawSource]) -> str:
        source_blocks = []
        latest_titles: list[str] = []
        top_liked_titles: list[str] = []
        for source in sources:
            metadata = self._json_dict(source.metadata_json)
            bucket = str(metadata.get("creator_bucket") or "")
            if bucket in {"latest", "both"}:
                latest_titles.append(source.title)
            if bucket in {"top_liked", "both"}:
                top_liked_titles.append(source.title)
            source_blocks.append(
                f"- [{source.title}]({source.canonical_url})\n"
                f"  - 标签：{bucket or '未标记'}"
            )
        latest_text = "、".join(latest_titles[:8]) or "暂无最新作品样本"
        top_liked_text = "、".join(top_liked_titles[:8]) or "暂无高赞作品样本"
        evidence = "\n".join(source_blocks)
        fallback_body = (
            "## 人设\n\n"
            f"围绕 {creator_name} 已入库作品做初步画像，后续可结合更多作品持续修正。\n\n"
            "## 选题\n\n"
            f"当前样本包含：{latest_text}。\n\n"
            "## 表达方式\n\n"
            "从标题和正文中归纳表达风格；当前版本保留来源证据，避免无依据扩写。\n\n"
            "## 受众\n\n"
            "根据选题和表达方式推断目标受众；资料不足时应以来源证据为准。\n\n"
            "## 商业价值\n\n"
            "结合高赞内容、稳定选题和受众匹配度评估潜在商业化方向。\n\n"
            "## 最新与高赞差异\n\n"
            f"- 最新样本：{latest_text}\n"
            f"- 高赞样本：{top_liked_text}\n"
        )
        body = fallback_body
        status_line = "> 生成状态：模型未成功返回，当前使用本地兜底模板；配置可用模型后可重新生成。"
        try:
            provider, model, provider_config = get_provider_runtime()
            if provider_config.get("api_style") != "mock" and not provider_config.get("base_url"):
                raise ValueError("model provider base_url is not configured")
            prompt = (
                f"请基于博主 {creator_name} 的已提取作品，生成博主分析。\n"
                "必须包含：人设、选题、表达方式、受众、商业价值、最新与高赞差异、来源引用。\n"
                "只能基于下面的资料，不足就明确说明不足，不能编造。\n\n"
                f"最新样本：{latest_text}\n"
                f"高赞样本：{top_liked_text}\n\n"
                f"来源资料：\n{evidence}"
            )
            generated = await provider.chat(
                [
                    {"role": "system", "content": "你是 StarMind 博主蒸馏分析专家。只能基于给定来源生成结构化分析。"},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=0.3,
            )
            if generated.strip():
                body = self._format_creator_analysis_body(generated, latest_text, top_liked_text)
                status_line = "> 生成状态：已调用模型完成博主分析。"
        except Exception as e:
            import logging
            logging.getLogger("starmind.wiki").warning(f"Creator analysis failed: {e}")
        return (
            f"# 博主分析：{creator_name}\n\n"
            f"{status_line}\n\n"
            f"- 博主标识：`{creator_key}`\n"
            f"- 来源数量：{len(sources)}\n\n"
            f"{body}\n\n"
            "## 来源引用\n\n"
            + evidence
            + "\n"
        )

    def _format_creator_analysis_body(self, generated: str, latest_text: str, top_liked_text: str) -> str:
        text = self._strip_transcript_sections(generated)
        text = re.sub(r"\r\n?", "\n", str(text or "")).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if not text:
            return ""
        if "## " not in text:
            sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", text) if part.strip()]
            grouped = "\n\n".join(sentences) if sentences else text
            text = f"## 综合分析\n\n{grouped}"
        required_sections = {
            "人设": "资料不足，暂无法进一步归纳人设。",
            "选题": "资料不足，暂无法进一步归纳选题。",
            "表达方式": "资料不足，暂无法进一步归纳表达方式。",
            "受众": "资料不足，暂无法进一步归纳受众。",
            "商业价值": "资料不足，暂无法进一步归纳商业价值。",
        }
        for heading, fallback in required_sections.items():
            if f"## {heading}" not in text:
                text += f"\n\n## {heading}\n\n{fallback}"
        if "最新与高赞差异" not in text:
            text += f"\n\n## 最新与高赞差异\n\n- 最新样本：{latest_text}\n- 高赞样本：{top_liked_text}"
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _strip_transcript_sections(self, value: str) -> str:
        text = re.sub(r"\r\n?", "\n", str(value or ""))
        text = re.sub(r"(?ims)^#{1,6}\s*逐字稿\s*\n.*?(?=^#{1,6}\s+|\Z)", "", text)
        text = re.sub(r"(?ims)^逐字稿\s*\n.*?(?=^#{1,6}\s+|\n\s*(?:标签|来源|链接|作者|生成时间|状态)：|\Z)", "", text)
        text = re.sub(r"(?ims)^片段：.*?(?=\n\s*(?:标签|来源|链接|作者|生成时间|状态)：|^#{1,6}\s+|\Z)", "", text)
        text = re.sub(r"(?ims)^链接解析结果.*?(?=^#{1,6}\s+|\n\s*(?:标签|来源|链接|作者|生成时间|状态)：|\Z)", "", text)
        text = re.sub(r"(?ims)^完整视频口播.*?(?=^#{1,6}\s+|\n\s*(?:标签|来源|链接|作者|生成时间|状态)：|\Z)", "", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _json_dict(self, raw_value: str | None) -> dict[str, Any]:
        try:
            value = json.loads(raw_value or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    async def _summarize(self, raw_source: RawSource, transcript: str, page_type: str) -> str:
        prompt_map = {
            "knowledge": (
                "你是一个知识提炼专家。请对下面的原始资料进行深度沉淀，输出精炼的知识卡片。\n\n"
                "【要求】\n"
                "1. 绝对不要照搬原文，必须用你自己的话重新组织\n"
                "2. 提炼出核心洞见和可复用结论，去除所有冗余和口语化表达\n"
                "3. 如果原文很长，只保留最有价值的 20% 精华内容\n"
                "4. 用结构化方式呈现，便于快速回顾\n\n"
                "【输出格式（Markdown）】\n"
                "## 一句话总结\n（用一句话概括这篇内容的核心价值）\n\n"
                "## 核心观点\n（列出 3-5 个最关键的观点/知识点，每个 1-2 句话）\n\n"
                "## 可复用结论\n（提炼出可以直接应用到实际场景的结论或方法）\n\n"
                "## 适用场景\n（这些知识在什么情况下有用）\n\n"
                "## 局限与注意\n（这些知识的边界、不适用的情况）\n"
            ),
            "methodology": (
                "你是一个方法论提炼专家。请把下面的原始资料提炼成简洁的方法论框架。\n\n"
                "【要求】\n"
                "1. 提炼判断框架和思考原则，不要写成操作清单\n"
                "2. 去除冗余，用精炼的语言描述\n"
                "3. 重点是「何时用」「怎么判断」「核心原则是什么」\n\n"
                "【输出格式（Markdown）】\n"
                "## 方法论名称\n\n"
                "## 适用场景\n\n"
                "## 核心原则（3-5 条）\n\n"
                "## 判断流程\n\n"
                "## 常见误区\n"
            ),
            "sop": (
                "你是一个 SOP 提炼专家。请把下面的原始资料整理成可执行的标准操作流程。\n\n"
                "【要求】\n"
                "1. 精简到最少必要步骤\n"
                "2. 每步必须是具体可执行的动作\n"
                "3. 去除所有废话和过渡语句\n\n"
                "【输出格式（Markdown）】\n"
                "## 目标\n\n"
                "## 前置条件\n\n"
                "## 步骤\n\n"
                "## 完成标准\n"
            ),
            "skill": (
                "请把下面原始资料沉淀成给 Agent 调用的 Skill 说明。"
                "结构：Skill 名称、调用场景、输入、步骤、输出格式、失败处理。"
            ),
        }
        prompt = (
            prompt_map.get(page_type, prompt_map["knowledge"])
            + f"\n\n---\n\n【原始资料】\n标题：{raw_source.title}\n来源：{raw_source.platform}\n链接：{raw_source.canonical_url}\n\n正文内容：\n{transcript[:8000]}"
        )
        try:
            provider, model, _config = get_provider_runtime()
            body = await provider.chat(
                [
                    {"role": "system", "content": "你是 StarMind 知识提炼专家。你的职责是把原始资料精炼成结构化知识卡片。绝对不要照搬原文，必须用精炼的语言重新组织，只保留最有价值的精华。"},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=0.3,
            )
            if not body.strip():
                raise ValueError("empty model response")
            status_line = "> 生成状态：已调用模型完成沉淀。"
        except Exception as e:
            import logging
            logging.getLogger("starmind.wiki").warning(f"LLM summarize failed: {e}")
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
