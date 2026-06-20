from __future__ import annotations

from app.connectors.base import BaseConnector, ConnectorItem


class MockConnector(BaseConnector):
    platform = "mock"

    async def login_check(self) -> bool:
        return True

    async def fetch_favorites_page(self, page_cursor=None):
        return {"items": self._items()}

    async def parse_items(self, page_html_or_json):
        return page_html_or_json.get("items", [])

    async def scan_until_boundary(self, connector_state: dict) -> list[ConnectorItem]:
        page = await self.fetch_favorites_page()
        return await self.parse_items(page)

    def _items(self) -> list[ConnectorItem]:
        return [
            ConnectorItem(
                raw_url="https://www.youtube.com/watch?v=agent001&utm_source=newsletter",
                title="Agentic Workflow: Tool Use Patterns",
                platform="youtube",
                author="AI Course",
                content_type="video",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://www.bilibili.com/video/BV1SM4y1K7ax?spm_id_from=333.999",
                title="RAG 系统从 0 到 1 实战教程",
                platform="bilibili",
                author="技术学习站",
                content_type="video",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://github.com/langchain-ai/langgraph?utm_medium=social",
                title="LangGraph Repository",
                platform="github",
                author="langchain-ai",
                content_type="repo",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/posts/startup-ai-pricing?utm_campaign=mock",
                title="AI SaaS 定价模型复盘",
                platform="web",
                author="Startup Notes",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/posts/team-sop-for-customer-interviews?share_source=wechat",
                title="用户访谈 SOP 模板",
                platform="web",
                author="Product Lab",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://www.youtube.com/shorts/funny998?utm_source=tiktok",
                title="猫咪突然跳上键盘的搞笑瞬间",
                platform="youtube",
                author="Fun Clips",
                content_type="short",
                metadata={"expected_label": "non_knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/deals/headphones-discount?utm_source=ad",
                title="今日耳机折扣汇总",
                platform="web",
                author="Deal Bot",
                metadata={"expected_label": "non_knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/opinion/ai-founder-lessons",
                title="一个 AI 创业者的 7 条经验",
                platform="web",
                author="Founder Diary",
                metadata={"expected_label": "uncertain"},
            ),
            ConnectorItem(
                raw_url="https://github.com/openai/openai-cookbook?utm_source=star",
                title="OpenAI Cookbook",
                platform="github",
                author="openai",
                content_type="repo",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://www.bilibili.com/video/BV1SM4y1K7ax?share_source=copy_link",
                title="RAG 系统从 0 到 1 实战教程（重复链接）",
                platform="bilibili",
                author="技术学习站",
                content_type="video",
                metadata={"expected_label": "duplicate"},
            ),
            ConnectorItem(
                raw_url="http://www.youtube.com/watch?v=rag2026&timestamp=100",
                title="RAG Evaluation Metrics Explained",
                platform="youtube",
                author="Search Lab",
                content_type="video",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/news/flash-ai-funding",
                title="某 AI 公司今日融资快讯",
                platform="web",
                author="News Flash",
                metadata={"expected_label": "uncertain"},
            ),
            ConnectorItem(
                raw_url="https://example.com/dance/trending-clip",
                title="热门舞蹈挑战合集",
                platform="web",
                author="Entertainment Hub",
                metadata={"expected_label": "non_knowledge"},
            ),
            ConnectorItem(
                raw_url="https://github.com/fastapi/fastapi",
                title="FastAPI Repository",
                platform="github",
                author="fastapi",
                content_type="repo",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/posts/obsidian-zettelkasten-method",
                title="Obsidian 笔记系统的卡片盒实践",
                platform="web",
                author="Knowledge Ops",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/random/mood",
                title="今天真的不想上班",
                platform="web",
                author="Personal Feed",
                metadata={"expected_label": "non_knowledge"},
            ),
            ConnectorItem(
                raw_url="https://www.youtube.com/watch?v=agent002&share_source=chat",
                title="Multi-Agent Memory Design",
                platform="youtube",
                author="AI Course",
                content_type="video",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/review/new-camera",
                title="新相机体验：值得买吗？",
                platform="web",
                author="Creator Tools",
                metadata={"expected_label": "uncertain"},
            ),
            ConnectorItem(
                raw_url="https://example.com/research/local-first-software",
                title="Local-first Software 论文和案例整理",
                platform="web",
                author="Research Notes",
                metadata={"expected_label": "knowledge"},
            ),
            ConnectorItem(
                raw_url="https://example.com/lottery/follow-and-win",
                title="关注抽奖赢周边",
                platform="web",
                author="Brand Account",
                metadata={"expected_label": "non_knowledge"},
            ),
        ]

