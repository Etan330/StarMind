# StarMind V2.1 开发 Spec

> 基于 PRD V2.1，面向开发实现的技术规格文档。每个 Goal 对应一个独立可交付的功能模块。

---

## Goal 1: Web Access — CDP 直连用户浏览器采集收藏列表

### 目标

替换当前 Playwright persistent context 方案，改为通过 CDP（Chrome DevTools Protocol）直连用户日常使用的 Chrome/Edge 浏览器，天然携带登录态，在后台 tab 中打开收藏页提取数据。用户无需安装任何插件。

### 当前现状

- `app/connectors/douyin.py` 使用 Playwright `launch_persistent_context` 打开独立 Chrome 实例
- 用户需要在独立窗口中手动登录
- 仅支持抖音一个平台

### 技术方案

#### 1.1 CDP 连接层（新文件 `app/connectors/cdp_proxy.py`）

```python
class CDPProxy:
    """直连用户已开启的 Chrome 浏览器（需用户以 --remote-debugging-port=9222 启动）"""

    async def connect(self, debug_url: str = "http://localhost:9222") -> None
    async def new_tab(self, url: str) -> CDPTab
    async def eval(self, tab: CDPTab, script: str) -> Any
    async def scroll(self, tab: CDPTab, distance: int = 800) -> None
    async def close_tab(self, tab: CDPTab) -> None
    async def get_cookies(self, tab: CDPTab, domain: str) -> list[dict]
```

- 使用 `httpx` + WebSocket 连接 CDP endpoint
- 不依赖 Playwright（减少依赖体积）
- 如果 9222 端口不可达，提示用户启动 Chrome 并给出命令

#### 1.2 平台 eval 脚本（新目录 `extension/`）

| 文件 | 平台 | 提取内容 |
|------|------|----------|
| `extension/douyin_eval.js` | 抖音 | `[{url, title, author}]` |
| `extension/bilibili_eval.js` | B站 | `[{url, title, author, bvid}]` |
| `extension/xiaohongshu_eval.js` | 小红书 | `[{url, title, author}]` |

每个脚本是纯 JS，通过 `Runtime.evaluate` 注入页面执行，返回 JSON 数组。

#### 1.3 平台连接器（改造 + 新增）

| 文件 | 变更 |
|------|------|
| `app/connectors/douyin.py` | 重构：内部改用 CDPProxy，保留 `extract_visible_video_links` 接口 |
| `app/connectors/bilibili.py` | 新增 |
| `app/connectors/xiaohongshu.py` | 新增 |

统一接口：

```python
class PlatformCollector:
    platform: str
    favorites_url: str

    async def extract_favorites(self, limit: int | None = None) -> list[ConnectorItem]
```

#### 1.4 连接检测 API

```
GET /api/cdp/status
→ {"connected": bool, "browser": "Chrome 126", "hint": "..."}
```

若浏览器未开启 remote debugging，返回 `connected: false` 并附带启动命令提示。

#### 1.5 路由变更

| 端点 | 变更 |
|------|------|
| `POST /douyin/browser/open` | 改用 CDP 连接，不再 launch 新实例 |
| `POST /bilibili/favorites/extract` | 新增 |
| `POST /xiaohongshu/favorites/extract` | 新增 |

#### 1.6 数据表变更

`connectors` 表新增字段：

```python
auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
auto_sync_cron: Mapped[str | None] = mapped_column(String(40), nullable=True, default="0 0 * * *")
```

### 验收标准

- [ ] CDP 连接用户 Chrome 成功，无需 Playwright
- [ ] 抖音/B站/小红书三平台收藏列表提取正常
- [ ] 提取结果进入 SyncService 正常流程（CandidateItem 创建）
- [ ] 浏览器未开启时给出明确提示

---

## Goal 2: 豆包网页版内容深度提取

### 目标

通过 CDP 打开豆包网页版（`www.doubao.com`），利用豆包多模态能力提取视频逐字稿和图文正文，替代本地 ASR。

### 当前现状

- `app/services/douyin_transcript_service.py` 使用本地 ASR 或标记 `audio_asr_pending`
- 很多视频内容只有链接没有实际内容

### 技术方案

#### 2.1 豆包提取器（新文件 `app/connectors/doubao_extractor.py`）

```python
class DoubaoExtractor:
    """通过 CDP 操控豆包网页版，发送链接获取内容提取结果"""

    async def check_login(self) -> bool
    async def extract_content(self, url: str, content_type: str = "auto") -> ExtractResult
    async def batch_extract(self, items: list[ConnectorItem], limit: int | None = None) -> list[ExtractResult]

@dataclass
class ExtractResult:
    url: str
    transcript: str      # 视频逐字稿
    text_content: str    # 图文正文
    title: str
    success: bool
    error: str | None = None
```

#### 2.2 豆包交互脚本（`extension/doubao_chat.js`）

```javascript
// 注入 doubao.com 页面：
// 1. 找到输入框 DOM
// 2. 填入 prompt + url
// 3. 点击发送
// 4. 等待生成完毕（检测 "停止生成" 按钮消失）
// 5. 提取最后一条回复内容
```

#### 2.3 Prompt 模板（`app/llm/prompts/doubao_extract.py`）

```python
PROMPTS = {
    "video": "请帮我提取这个链接的完整逐字稿内容，保留原始表述，不要总结：{url}",
    "article": "请帮我提取这个链接中的所有文字内容，包括正文、标题和关键信息：{url}",
    "auto": "请帮我提取这个链接的全部内容，如果有视频请提取逐字稿，如果有图文请提取文字：{url}",
}
```

#### 2.4 集成到处理流程

修改 `app/services/raw_source_service.py`：

```python
class RawSourceService:
    async def ingest_candidate(self, candidate_id: int, extractor: DoubaoExtractor | None = None) -> RawSource:
        # 如果 candidate 没有 transcript/content → 调用 DoubaoExtractor
        # 提取成功 → 写入 raw_sources/{platform}/{id}/transcript.md
        # 提取失败 → 降级为 metadata_only 质量等级
```

#### 2.5 路由

```
POST /api/extract/doubao
Body: {"candidate_ids": [1,2,3], "content_type": "auto"}
→ {"results": [...], "success_count": N, "failure_count": M}
```

#### 2.6 降级策略

| 场景 | 降级方案 |
|------|----------|
| 豆包未登录 | 提示用户登录，暂停提取 |
| 豆包响应超时（>60s） | 跳过当前条目，标记 `extraction_timeout` |
| 豆包返回空内容 | 标记 `metadata_only`，保留链接记录 |
| CDP 断连 | 中断批量操作，已完成的保留 |

### 验收标准

- [ ] 能通过 CDP 连接到已登录的豆包网页版
- [ ] 视频链接成功提取逐字稿
- [ ] 图文链接成功提取文字内容
- [ ] 批量提取支持中断恢复（已完成的不丢失）
- [ ] 未登录/超时等异常有明确降级

---

## Goal 3: 历史收藏前置分类筛选

### 目标

用户首次连接收藏夹时，历史内容量大。先轻量提取标题列表 → LLM 批量分类 → 弹窗让用户选择保留的分类 → 仅对保留分类的内容进行深度抓取。

### 技术方案

#### 3.1 轻量扫描接口

```
POST /api/sync/scan-titles
Body: {"platform": "bilibili", "limit": 500}
→ {"items": [{"url": "...", "title": "...", "author": "..."}], "total": 328}
```

仅用 CDP eval 提取标题+链接+作者，不提取内容。

#### 3.2 批量标题分类

```
POST /api/classify/batch-titles
Body: {"items": [...]}
→ {
    "categories": [
      {"domain": "AI/大模型", "count": 32, "items": [...]},
      {"domain": "搞笑视频", "count": 45, "items": [...]},
      ...
    ]
  }
```

实现在 `app/services/classifier_service.py` 新增方法：

```python
class ClassifierService:
    async def batch_classify_titles(self, items: list[dict]) -> list[TitleClassification]:
        """基于标题的轻量分类，每批 20 条发给 LLM"""
```

#### 3.3 用户确认选择

```
POST /api/sync/confirm-categories
Body: {
  "platform": "bilibili",
  "selected_domains": ["AI/大模型", "产品设计", "编程开发"],
  "items": [...]  // 仅选中分类下的 items
}
→ 将选中 items 写入 CandidateItem（status: pending_classification）
→ 未选中 items 写入 SyncLedger（标记 classification_label: "user_skipped"）
```

#### 3.4 前端弹窗页面

新增模板 `app/templates/category_select.html`：

- 展示分类列表 + 每个分类下的条目数
- 复选框选择要保留的分类
- 全选 / 取消全选
- 确认导入按钮

#### 3.5 状态流

```
CDP 提取标题列表
  → POST /api/sync/scan-titles
  → POST /api/classify/batch-titles
  → 渲染分类弹窗
  → 用户勾选
  → POST /api/sync/confirm-categories
  → 选中 items → CandidateItem → 后续正常 Ingest 流程
  → 未选中 items → SyncLedger (user_skipped，不深度抓取)
```

### 验收标准

- [ ] 能一次性提取 500 条收藏标题列表
- [ ] LLM 批量分类返回合理的分类结果
- [ ] 弹窗正确展示分类 + 条目数
- [ ] 仅选中分类的 items 进入深度抓取流程
- [ ] 未选中的 items 记录在 SyncLedger，不重复扫描

---

## Goal 4: 每日零点自动同步

### 目标

对已连接的收藏夹平台，每日零点自动触发增量同步。

### 技术方案

#### 4.1 调度器（新文件 `app/scheduler.py`）

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def daily_sync_job():
    """遍历所有 auto_sync_enabled=True 的 Connector，触发增量同步"""
    async with get_async_db() as db:
        connectors = db.query(Connector).filter(Connector.auto_sync_enabled == True).all()
        for connector in connectors:
            try:
                await SyncService(db).scan_connector(connector.id)
            except Exception as e:
                log_scan_error(connector.id, str(e))
                # 30 分钟后重试
                scheduler.add_job(retry_sync, 'date', run_date=now()+timedelta(minutes=30), args=[connector.id], id=f"retry_{connector.id}")
```

#### 4.2 集成到 FastAPI 生命周期

```python
# app/main.py
from app.scheduler import scheduler

def create_app() -> FastAPI:
    ...
    @app.on_event("startup")
    async def start_scheduler():
        scheduler.add_job(daily_sync_job, 'cron', hour=0, minute=0, id='daily_sync')
        scheduler.start()

    @app.on_event("shutdown")
    async def stop_scheduler():
        scheduler.shutdown()
```

#### 4.3 重试机制

| 条件 | 策略 |
|------|------|
| 同步失败 | 30 分钟后重试 |
| 最多重试次数 | 3 次 |
| 3 次都失败 | 标记 connector `status = "sync_failed"`，等下次定时 |

#### 4.4 依赖新增

`requirements.txt` 增加：

```
apscheduler>=3.10.0
```

#### 4.5 管理 API

```
POST /api/sync/toggle-auto   Body: {"connector_id": 1, "enabled": true}
GET  /api/sync/schedule       → {"next_run": "...", "connectors": [...]}
```

### 验收标准

- [ ] 应用启动后 APScheduler 正确注册定时任务
- [ ] 零点触发后对已启用的 connector 执行增量同步
- [ ] 失败重试最多 3 次，不死循环
- [ ] 可通过 API 开关自动同步

---

## Goal 5: 新手引导系统

### 目标

新用户首次使用时，自动触发全链路引导：连接收藏夹 → 同步分类 → 偏好设置 → Push 设置。

### 技术方案

#### 5.1 数据模型

新增 `OnboardingStatus` 模型到 `app/models/records.py`：

```python
class OnboardingStatus(Base):
    __tablename__ = "onboarding_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)  # 0=未开始, 1-6 对应引导步骤
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
```

#### 5.2 引导流程 API

```
GET  /api/onboarding/status    → {"current_step": 0, "completed": false, "skipped": false}
POST /api/onboarding/advance   Body: {"step": 2, "data": {...}}
POST /api/onboarding/skip      → 标记 skipped=True
POST /api/onboarding/reset     → 重置为 step=0
```

#### 5.3 前端页面

新增 `app/templates/onboarding.html`：

- Step 1: 欢迎页（开始 / 跳过）
- Step 2: 选择平台（复选框：抖音、小红书、B站）
- Step 3: 同步进度 + 分类弹窗（复用 Goal 3 的 category_select）
- Step 4: 偏好设置（复用 Goal 6 的偏好滑块）
- Step 5: Push 时间设置（复用 Goal 6 的推送配置）
- Step 6: 完成页

#### 5.4 触发逻辑

在首页路由 `GET /` 中：

```python
if not onboarding_completed(db) and not onboarding_skipped(db):
    return RedirectResponse("/ui/onboarding")
```

#### 5.5 "重新查看引导"

设置页增加按钮，调用 `POST /api/onboarding/reset` 后跳转到引导页。

### 验收标准

- [ ] 首次访问自动跳转引导页
- [ ] 可跳过引导
- [ ] 完成引导后不再自动弹出
- [ ] 设置页可重新触发引导

---

## Goal 6: 智能推送系统 + 偏好学习

### 目标

基于用户对各领域的偏好权重，定时推送知识内容。Like/Unlike 反馈动态调整偏好。

### 技术方案

#### 6.1 数据模型

新增到 `app/models/records.py`：

```python
class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    score: Mapped[int] = mapped_column(Integer, default=50)  # 0-100
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class PushSettings(Base):
    __tablename__ = "push_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_time: Mapped[str] = mapped_column(String(5), default="08:00")  # HH:MM
    end_time: Mapped[str] = mapped_column(String(5), default="22:00")
    frequency_hours: Mapped[int] = mapped_column(Integer, default=4)
    items_per_push: Mapped[int] = mapped_column(Integer, default=3)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)

class PushHistory(Base):
    __tablename__ = "push_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_source_id: Mapped[int] = mapped_column(ForeignKey("raw_sources.id"), nullable=False)
    pushed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "like" | "unlike" | null
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

#### 6.2 偏好服务（新文件 `app/services/preference_service.py`）

```python
class PreferenceService:
    def get_all(self) -> list[UserPreference]
    def set_score(self, domain: str, score: int) -> None
    def apply_feedback(self, domain: str, feedback: str) -> None:
        """like → +2, unlike → -3, 连续规则见 PRD 8.5"""
```

#### 6.3 Push Agent（新文件 `app/agent/push_agent.py`）

```python
class PushAgent:
    async def generate_push(self) -> list[PushItem]:
        """
        1. 检查时间区间和暂停状态
        2. 按偏好权重加权随机抽取 RawSource
        3. 排除近期已推送过的
        4. 返回推送列表
        """

    async def handle_feedback(self, push_id: int, feedback: str) -> None:
        """记录反馈 + 调整偏好分数"""
```

#### 6.4 推送调度

集成到 `app/scheduler.py`：

```python
async def push_check_job():
    """每小时检查一次，若在推送时间窗口内则生成推送"""
    settings = get_push_settings()
    if settings.is_paused or not in_time_window(settings):
        return
    items = await PushAgent(db).generate_push()
    save_push_items(items)
```

#### 6.5 API 路由

```
GET  /api/push/current          → 当前待查看的推送列表
POST /api/push/feedback         Body: {"push_id": 1, "feedback": "like"}
GET  /api/preferences           → [{"domain": "AI/大模型", "score": 85}, ...]
POST /api/preferences           Body: {"domain": "AI/大模型", "score": 85}
GET  /api/push/settings         → PushSettings
POST /api/push/settings         Body: {start_time, end_time, frequency_hours, ...}
```

#### 6.6 前端页面

| 页面 | 内容 |
|------|------|
| `preferences.html` | 领域列表 + 滑块（0-100） |
| `push_settings.html` | 时间选择器 + 频率下拉 + 暂停开关 |
| 首页推送卡片 | 标题 + 来源 + 收藏时间 + Like/Unlike 按钮 |

### 验收标准

- [ ] 偏好滑块保存/读取正常
- [ ] 推送在时间窗口内按偏好权重生成内容
- [ ] Like/Unlike 反馈正确调整偏好分数
- [ ] 暂停开关生效
- [ ] 连续反馈规则（3次unlike降至10%，5次like升至90%）正确触发

---

## Goal 7: 知识图谱 — 星链可视化

### 目标

知识库内容自动建立关联，形成网状结构。首页用 ECharts 力导向图渲染"星链"效果。

### 技术方案

#### 7.1 数据模型

新增到 `app/models/records.py`：

```python
class KnowledgeGraphEdge(Base):
    __tablename__ = "knowledge_graph_edges"
    __table_args__ = (
        Index("ux_graph_edge", "source_id", "target_id", "relation", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("raw_sources.id"), nullable=False)
    target_id: Mapped[int] = mapped_column(ForeignKey("raw_sources.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String(80), nullable=False)  # topic_overlap | domain_same | concept_ref | author_same
    weight: Mapped[float] = mapped_column(Float, default=0.5)
    shared_concepts_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
```

#### 7.2 图谱服务（新文件 `app/services/graph_service.py`）

```python
class GraphService:
    def build_edges_for_source(self, raw_source_id: int) -> list[KnowledgeGraphEdge]:
        """Ingest 时调用：提取 related_concepts，与现有节点匹配建边"""

    def get_graph_data(self, domain_filter: str | None = None) -> dict:
        """返回 {nodes: [...], edges: [...]} 供前端渲染"""

    def detect_orphans(self) -> list[int]:
        """Lint 用：找出无边连接的孤立节点"""
```

#### 7.3 Ingest 时建边

修改 `app/services/wiki_service.py` 的 `create_page_from_raw_source`：

```python
# 在 LLM 生成 Wiki 页面的同时，要求输出 related_concepts
# 用这些 concepts 匹配已有 RawSource 的 topics/concepts
# 建立 KnowledgeGraphEdge
```

LLM 输出 schema 扩展：

```json
{
  "title": "...",
  "summary": "...",
  "bullets": [...],
  "related_concepts": ["RAG", "向量数据库", "Embedding"],
  "domain": "AI/大模型",
  "topics": ["RAG", "检索增强生成"]
}
```

#### 7.4 API

```
GET /api/graph                → {nodes: [...], edges: [...]}
GET /api/graph?domain=AI      → 按领域筛选
GET /api/graph/node/{id}      → 单个节点详情 + 关联节点
```

#### 7.5 前端页面（`app/templates/graph.html`）

- ECharts `force` 力导向布局
- 节点大小 = 关联边数量
- 节点颜色 = domain 映射
- 连线粗细 = weight
- 交互：悬停显示摘要、点击展开详情、拖拽、缩放、领域筛选下拉
- 节点 > 500 时按 domain 聚类折叠

#### 7.6 性能考虑

- 图谱数据接口做缓存（JSON 文件 `local_data/config/graph_cache.json`），Ingest 时失效
- 前端分页加载：首屏最多 200 节点，展开时按需加载

### 验收标准

- [ ] Ingest 新 RawSource 时自动提取 concepts 并建边
- [ ] `/api/graph` 返回正确的 nodes + edges 结构
- [ ] ECharts 力导向图渲染正常，交互流畅
- [ ] 领域筛选正常工作
- [ ] 节点过多时聚类折叠不卡顿

---

## Goal 8: Lint Agent — 知识健康检查

### 目标

定期检查 Wiki 健康状态：矛盾、过期、孤立页面、重复、知识缺口。

### 技术方案

#### 8.1 Lint 检查项

| 检查 | 说明 | 输出 |
|------|------|------|
| 孤立节点 | 图谱中无边连接的 RawSource | 建议建立关联 |
| 单来源主题 | 某 domain 只有 1 个 RawSource | 推荐补充方向 |
| 过期页面 | Wiki 页面超过 90 天未更新 | 建议刷新 |
| 重复检测 | 标题/concepts 高度相似的页面 | 建议合并 |
| 来源缺失 | Wiki 页面引用的 RawSource 不存在 | 标记异常 |

#### 8.2 实现（新文件 `app/agent/lint_agent.py`）

```python
class LintAgent:
    async def run_full_check(self) -> LintReport:
        """执行所有检查，返回报告"""

    async def run_check(self, check_type: str) -> list[LintFinding]:
        """执行单项检查"""

@dataclass
class LintFinding:
    check_type: str
    severity: str      # "info" | "warning" | "error"
    target_type: str   # "raw_source" | "wiki_page" | "graph_node"
    target_id: str
    message: str
    suggestion: str
```

#### 8.3 调度

- Cron 每日执行一次（凌晨 3:00，错开同步任务）
- 手动触发：`POST /api/lint/run`

#### 8.4 API

```
POST /api/lint/run              → 手动触发
GET  /api/lint/report           → 最近一次报告
GET  /api/lint/findings?type=X  → 按类型筛选
```

### 验收标准

- [ ] 5 项检查均正确执行
- [ ] 报告输出包含具体 target 和建议
- [ ] 定时调度正常工作
- [ ] 手动触发 API 响应正常

---

## 依赖变更汇总

```
# requirements.txt 新增
apscheduler>=3.10.0
websockets>=12.0     # CDP WebSocket 连接
```

## 数据库迁移汇总

| 新增表 | Goal |
|--------|------|
| `onboarding_status` | Goal 5 |
| `user_preferences` | Goal 6 |
| `push_settings` | Goal 6 |
| `push_history` | Goal 6 |
| `knowledge_graph_edges` | Goal 7 |

| 修改表 | 变更 | Goal |
|--------|------|------|
| `connectors` | +`auto_sync_enabled`, +`auto_sync_cron` | Goal 4 |

---

## 开发顺序建议

```
Goal 1 (CDP 采集) ← 基础设施，其他 Goal 依赖
  ↓
Goal 2 (豆包提取) ← 依赖 CDP 连接层
  ↓
Goal 3 (分类筛选) ← 依赖 Goal 1 的标题提取
  ↓
Goal 4 (定时同步) ← 依赖 Goal 1 的 connector 改造
  ↓
Goal 5 (新手引导) ← 串联 Goal 1/3/6
  ↓
Goal 6 (推送+偏好) ← 独立，可并行
  ↓
Goal 7 (知识图谱) ← 依赖 Ingest 流程
  ↓
Goal 8 (Lint Agent) ← 依赖 Goal 7 的图谱数据
```
