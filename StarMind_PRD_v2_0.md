# StarMind PRD V2.0

> **主动知识筛选与自维护 Agent — 完整产品需求文档**

| 字段 | 内容 |
|------|------|
| 文档版本 | V2.0 |
| 更新日期 | 2026-06-22 |
| 定位 | 黑客松参赛项目 / 可落地产品原型 |
| 技术栈 | Python + FastAPI + 自研 AgentRunner + Jinja2 SSR |
| 采集方案 | Web Access（CDP 直连用户浏览器）+ 手动粘贴（永久保留） |
| 仓库 | https://github.com/Etan330/StarMind.git |

---

## 0. 产品 Pitch

### 一句话定义

**StarMind 把用户分散在各平台收藏夹里的知识类内容，自动筛选、编译并维护成一个持续迭代的个人 LLM Wiki。**

### Slogan

> **"收藏即知识，StarMind 让你的收藏夹自己成长。"**

### 产品愿景

让收藏夹从"沉睡的信息仓库"变成"会自我编译、自我维护、自我进化的知识系统"。用户只负责收藏和提出问题；StarMind 负责筛选知识、维护结构、更新连接、发现缺口。

---

## 1. 产品差异化与竞品对比

| 维度 | StarMind | Rewind/Limitless | Mem.ai | Fabric | Obsidian+Copilot |
|------|----------|------------------|--------|--------|------------------|
| 采集方式 | **主动同步收藏夹** | 被动录屏录音 | 手动输入 | CLI 喂入 | 手动写笔记 |
| 智能过滤 | ✅ 两层分类器 | ❌ 全量 | ❌ 全量 | ❌ | ❌ |
| 自维护 | ✅ Ingest/Query/Lint | ❌ | 部分 | ❌ 无状态 | ❌ |
| 原始资料保护 | ✅ 不可变分层 | ✅ | ❌ 可编辑 | ❌ | 取决于用户 |
| 知识图谱 | ✅ 自动构建 | ❌ | 部分 | ❌ | 手动链接 |
| 隐私 | **本地优先** | 云端 | 云端 | 本地 | 本地 |
| 多模型支持 | ✅ 10 家 LLM | ❌ | ❌ | ❌ | 单一模型 |

**核心差异化：** 唯一同时做到"主动采集 + 智能过滤 + 不可变原始资料 + Agent 自维护 Wiki + 本地优先"的产品。

---

## 2. 核心问题定义

| # | 问题 | 说明 |
|---|------|------|
| 01 | 收藏夹≠知识库 | 混有知识、娱乐、消费，不能全量入库 |
| 02 | 用户已粗筛 | 收藏行为=初筛，系统只需二次分类 |
| 03 | 维护是长期成本 | 知识库失效=没人更新、去重、维护连接 |
| 04 | 同步独立于知识库 | Wiki 会被合并/重写，不能用来判断已抓取 |

---

## 3. 核心架构原则

### 3.1 原始资料不可变，派生知识可迭代

| 层级 | Agent 权限 |
|------|-----------|
| Raw Sources（原始资料） | ❌ 只能追加、索引、引用。`immutable=True, agent_delete_allowed=False` |
| Wiki / SOP / 方法论（派生知识） | ✅ 可合并、重写、删除、重构 |

### 3.2 同步独立于知识库

去重依赖 Raw Sources + Sync Ledger（两者不可被 Agent 删除），不依赖 Wiki 层。

### 3.3 边界链接停止爬取

每轮扫描从收藏页顶部开始读取，遇到已存在于 Raw Sources / Sync Ledger 的链接即停止。

### 3.4 本地优先

所有数据存本地（`local_data/`），Cookie/API Key 不上云，支持离线使用。

---

## 4. 数据采集方案

### 4.1 采集通道架构

```
┌─────────────────────────────────────────────────────────────┐
│                    StarMind Agent Core                        │
│              (Python 后端，所有智能逻辑在此)                   │
└───────────────────────────┬──────────────────────────────────┘
                            │
                   统一 API：POST /api/sync/push
                            │
              ┌─────────────┼─────────────────┐
              │             │                 │
       ┌──────▼──────┐  ┌──▼───────────┐  ┌──▼──────────┐
       │ Web Access  │  │ 手动粘贴     │  │ IM 机器人   │
       │(CDP 直连    │  │(永久保留)    │  │(未来扩展)   │
       │ 用户浏览器) │  │ 兜底入口     │  │             │
       └─────────────┘  └──────────────┘  └─────────────┘
```

### 4.2 Web Access（CDP 直连用户浏览器）— 主力采集方案

**原理：** 通过 CDP（Chrome DevTools Protocol）直连用户日常使用的 Chrome/Edge 浏览器，天然携带登录态，在后台 tab 中打开收藏页，读取 DOM 提取数据。**用户无需安装任何插件。**

**核心优势 vs Playwright vs 浏览器扩展：**

| 维度 | Web Access (CDP) | Playwright（旧方案） | 浏览器扩展 |
|------|------------------|---------------------|-----------|
| 用户安装 | **无需安装任何东西** | 无需安装 | 需装插件 |
| 登录态 | ✅ 直连用户浏览器，天然有 | 需弹窗登录 | ✅ 天然有 |
| 反爬风险 | 极低（等同用户操作） | 中（有指纹） | 极低 |
| 部署依赖 | 只需 Node.js | 需 Chromium 1.5GB | 纯客户端 |
| 对用户浏览器侵入 | 最小（后台 tab，完成后关闭） | 弹独立窗口 | 后台运行 |
| 开发语言 | JS（通过 curl 调 HTTP API） | Python | JS |
| Agent 集成 | ✅ Agent 直接 curl 调用 | ✅ Python 调用 | 需中间 API |

**工作方式：**

```
StarMind Agent 发起采集任务
  → 调用 CDP Proxy HTTP API (localhost:3456)
  → /new 打开目标平台收藏页（后台 tab）
  → /eval 执行 JS 提取收藏列表 DOM
  → /scroll 滚动加载更多
  → /eval 检测边界链接 → 停止
  → /close 关闭 tab
  → 将结果写入 Candidate Pool
```

**各平台采集方式：**

| 平台 | CDP 操作 | 状态 |
|------|----------|------|
| B站 | 打开收藏页 → eval 提取视频列表 → scroll 翻页 | P0 |
| 抖音 | 打开收藏页 → eval 提取视频/笔记链接 | P0（已有 DOM 解析逻辑可复用） |
| 小红书 | 打开收藏页 → eval 提取笔记列表 | P1 |
| YouTube | 打开 Liked Videos → eval 提取 | P1 |
| 知乎 | 打开收藏夹 → eval 提取 | P1 |
| X (Twitter) | 打开 Bookmarks → eval 提取 | P2 |
| 微信公众号 | 打开阅读历史 → eval 提取 | P2（网页版受限） |

**CDP Proxy API（Agent 侧调用示例）：**

```bash
# 打开 B 站收藏页
TAB=$(curl -s -X POST --data-raw 'https://space.bilibili.com/xxx/favlist' http://localhost:3456/new)

# 提取收藏列表
curl -s -X POST "http://localhost:3456/eval?target=$TAB" -d '
  Array.from(document.querySelectorAll(".fav-video-list .small-item")).map(el => ({
    title: el.querySelector(".title")?.textContent?.trim(),
    url: el.querySelector("a")?.href,
    author: el.querySelector(".author")?.textContent?.trim()
  }))
'

# 滚动加载更多
curl -s "http://localhost:3456/scroll?target=$TAB&direction=bottom"

# 关闭 tab
curl -s "http://localhost:3456/close?target=$TAB"
```

**与 Agent Core 集成：**

Sync Agent 通过 Python `subprocess` 或 `httpx` 调用 CDP Proxy 的 HTTP API（localhost:3456），无需额外框架。现有 douyin.py 的 DOM 解析逻辑可直接复用为 `/eval` 的 JS 脚本。

### 4.3 手动粘贴（Passive API）— 永久保留

```
POST /api/passive/ingest
输入：URL 或 URL 列表
→ 进入 Candidate Pool (source_type = "passive")
→ 正常 Classify → Extract → Ingest 流程
```

**适用场景：**
- 微信公众号文章（只能复制链接）
- 任何无法自动抓取的封闭平台
- IM 机器人转发的链接
- V3 统一输入路由（支持 link / idea / creator / favorites 四种模式）

---

## 5. 知识分类器（两层分类体系）

### 5.1 第一层：是否知识（过滤层）

| 标签 | 含义 | 处理 |
|------|------|------|
| `knowledge` | 知识/干货/教程/方法论 | → 第二层分类 → Raw Sources |
| `uncertain` | 可能有价值，信息不足 | → 待确认区（用户处理） |
| `non_knowledge` | 文娱/消费/低信息量 | → 回收站（30 天可恢复） |

**实现方式：** LLM JSON 输出 + 启发式关键词兜底（双保险）

### 5.2 第二层：细粒度领域分类

| 字段 | 说明 | 示例 |
|------|------|------|
| `domain` | 知识领域 | "AI/大模型"、"产品设计" |
| `topics` | 具体主题标签 | ["RAG", "向量数据库"] |
| `content_form` | 内容形式 | 教程 / 方法论 / 案例 / 工具 |
| `depth_level` | 深度等级 | 入门 / 进阶 / 深度 |

### 5.3 标签体系演进

| 阶段 | 策略 |
|------|------|
| 冷启动 | AI 自由分类，不受预设约束 |
| 标签成长 | 新内容优先匹配已有标签，不匹配时创建 |
| 用户干预 | 可合并/重命名/调整层级，修正反馈给 LLM |

### 5.4 工程阈值

| 置信度 | 动作 |
|--------|------|
| ≥ 0.75 | 自动入库 |
| 0.45 ~ 0.75 | 待确认 |
| < 0.45 | 剔除（进回收站） |

---

## 6. LLM Wiki 维护逻辑（Karpathy 模式）

### 6.1 四层结构

| 层级 | Agent 权限 |
|------|-----------|
| Raw Sources | ❌ 不可修改/删除 |
| Source Summary | 可重新生成 |
| Wiki / SOP / Methodology | ✅ 可合并/删除/重写 |
| Schema（规则层） | 人定义，Agent 遵守 |

### 6.2 三个核心操作

| 操作 | 含义 |
|------|------|
| **Ingest** | 新 Raw Source → 生成 summary → 更新/创建 Wiki 页面（4 种类型：knowledge/methodology/sop/skill） |
| **Query** | 基于 Wiki + Raw Sources 回答问题，高价值回答写回 Wiki |
| **Lint** | 定期检查矛盾、过期、孤立页面、重复、缺口 |

### 6.3 Quality Gate（6 级质量评估）

Wiki 页面生成后需通过质量检查：

| 等级 | 含义 | 动作 |
|------|------|------|
| ready | 完整可用 | 标记 active |
| needs_source_check | 来源引用不足 | 提示补充 |
| asr_pending | 等待音频转写 | 后台 ASR |
| metadata_only | 只有标题无正文 | 提示用户确认 |
| fallback | LLM 生成失败，使用模板 | 标记待重试 |
| failed | 生成失败 | 进入错误队列 |

---

## 7. 增量同步：边界链接停止爬取

### 7.1 扫描逻辑

```
Agent 通过 CDP Proxy 发起采集：
  1. /new 打开收藏页（后台 tab）
  2. /eval 提取收藏项列表
  3. 提取 external_item_id + canonical_url
  4. 调用后端检查是否已存在于 Raw Sources / Sync Ledger
  5. 存在 → 到达边界，停止
  6. 不存在 → 写入 Candidate Pool
  7. /scroll 滚动加载更多 → 重复 2-6
  8. /close 关闭 tab
  9. 更新 Sync Ledger
```

### 7.2 URL 标准化

- 去掉追踪参数（utm_source、spm_id_from 等）
- 统一 http/https、移动端/桌面端域名
- 提取平台稳定 ID（B站 BV ID、YouTube video_id 等）
- 优先用 `platform + external_item_id` 作唯一键

### 7.3 安全兜底

| 异常 | 策略 |
|------|------|
| 未遇到旧链接 | 最多扫描 max_scan_pages 后停止 |
| 被判 non_knowledge 的旧内容 | 同时查 Sync Ledger |

---

## 8. Multi-Agent 架构设计

### 8.1 编排架构

```
┌──────────────────────────────────────────────────┐
│              AgentRunner (自研编排)                │
│       (状态管理 + API 路由串联 + 任务调度)         │
└────┬────────────────┬────────────────┬───────────┘
     │                │                │
     ▼                ▼                ▼
┌──────────┐   ┌───────────┐   ┌───────────┐
│ Pipeline │   │   Query   │   │   Lint    │
│ Chain    │   │   Agent   │   │   Agent   │
└────┬─────┘   └───────────┘   └───────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  Sync → Classify → Extract → Ingest     │
└─────────────────────────────────────────┘
```

### 8.2 Agent 定义（6 个核心 Agent）

| Agent | 职责 | 触发 | LLM |
|-------|------|------|-----|
| **Sync Agent** | 接收采集数据，边界链接停止 | Web Access CDP 触发 / 手动触发 | 无 |
| **Classifier** | 两层分类（是否知识 + 领域标签） | item.discovered | DeepSeek / GPT-4o-mini |
| **Extractor** | 正文/逐字稿提取（支持 ASR） | item.classified | yt-dlp + faster-whisper |
| **Ingest Agent** | Raw Source → Wiki 编译 | item.extracted | DeepSeek / Claude Sonnet |
| **Query Agent** | 用户问答 + 知识检索 | 用户触发 | DeepSeek / Claude Sonnet |
| **Lint Agent** | Wiki 健康检查与修复 | Cron(daily) / 手动 | DeepSeek / GPT-4o |

### 8.3 状态机

```
DISCOVERED → CLASSIFYING ┬→ KNOWLEDGE → EXTRACTING → RAW_SOURCED → INGESTING → INGESTED
                         ├→ UNCERTAIN → (待确认队列)
                         └→ NON_KNOWLEDGE → ARCHIVED (回收站，30天可恢复)
```

### 8.4 Activation Rules（知识激活）

| 规则 | 触发 | 功能 |
|------|------|------|
| 每日知识唤醒 | 每天打开时 | 从近期/长期未读资料推送 3 条 |
| 上下文激活 | 用户提问时 | 主动找出相关历史资料 |
| 每周专项归纳 | 每周日 | 近期收藏归纳成专题/SOP |

### 8.5 Agent Legion（子 Agent 扩展）

| Sub-Agent | 职责 | 执行频率 |
|-----------|------|----------|
| 方法论研究员 | 归纳收藏为可复用方法论 | 每周 |
| SOP 架构师 | 高频流程沉淀为 SOP | 每两周 |
| 灵感整理员 | 整理手动录入的想法 | 按需 |

---

## 9. 技术选型

| 层级 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.11+ | 团队擅长 |
| Web 框架 | FastAPI | 异步 + 自动文档 |
| Agent 编排 | 自研 AgentRunner | 轻量可控，已实现 |
| LLM 抽象 | 自研 LLMProvider + Registry | 统一接口，支持 10 家供应商 |
| 默认 LLM | DeepSeek (deepseek-v4-flash) | 国内可用，低成本 |
| 数据库 | SQLite（MVP）→ PostgreSQL（生产） | 零部署 |
| 向量检索 | ChromaDB（待补充） | 当前是关键词匹配 |
| 正文提取 | yt-dlp + faster-whisper（视频 ASR） | 已实现 |
| 前端 | Jinja2 SSR（当前）/ React（未来） | 26 个页面已完成 |
| 采集 | Web Access CDP（直连用户浏览器） | 零安装，天然登录态 |
| 部署 | 本地 uvicorn → Docker Compose | 本地优先 |

### 支持的 LLM 供应商

| 供应商 | API 风格 | 状态 |
|--------|----------|------|
| DeepSeek | OpenAI 兼容 | ✅ 默认 |
| OpenAI | 原生 | ✅ |
| Anthropic | 原生 | ✅ |
| Google Gemini | 原生 | ✅ |
| GLM (智谱) | OpenAI 兼容 | ✅ |
| 豆包 (Doubao) | OpenAI 兼容 | ✅ |
| MiniMax | OpenAI 兼容 | ✅ |
| Kimi (Moonshot) | OpenAI 兼容 | ✅ |
| 通义千问 (Qwen) | OpenAI 兼容 | ✅ |
| 小米 MIMO | OpenAI 兼容 | ✅ |

---

## 10. 数据模型

### 核心表

| 表 | 作用 | 关键约束 |
|----|------|----------|
| connectors | 平台连接器注册 | last_boundary_url |
| sync_ledger_items | 同步账本（去重核心） | platform+external_item_id 唯一 |
| candidate_items | 收藏候选项 | status 状态流转 |
| knowledge_classifications | 分类审计 | label, confidence, reason |
| raw_sources | 原始资料 | **immutable=True, agent_delete_allowed=False** |
| recycle_bin_items | 可恢复回收站 | expires_at (30天) |
| wiki_pages | 知识库页面 | page_type, source_refs, version |
| wiki_logs | 操作日志 | operation, target_id |

### 本地文件结构

```
local_data/
├── starmind.db              # SQLite 数据库
├── raw_sources/             # 不可变原始资料
│   └── {platform}/{id}/
│       ├── metadata.json
│       ├── transcript.md
│       ├── raw.md
│       └── clean.md
├── wiki/                    # 派生知识
├── sop/
├── methodology/
├── recycle_bin/
├── logs/agent_runs.jsonl    # Agent trace
├── browser/                 # 浏览器 session（如需）
└── config/
    ├── model_config.json
    ├── model_profiles.json
    ├── providers.json
    ├── secrets.json         # API Keys（不提交）
    └── activation_rules.json
```

---

## 11. 新增亮点功能

### 11.1 知识图谱实时展示

- Ingest 时输出 `related_concepts`
- 后端维护 graph edges
- 前端用 ECharts 力导向图渲染
- 节点=概念/主题，大小=关联来源数量

### 11.2 智能知识缺口检测

Lint Agent 分析：
- 哪些主题只有 1 个来源
- 哪些概念缺专门页面
- 输出"推荐补充方向"

### 11.3 知识日报/周报

结合 Activation Rules，自动生成：新增知识 + Wiki 变更 + 缺口提醒 + 健康状态

### 11.4 对话式知识探索

Query Agent 多轮对话，回答带来源引用溯源

### 11.5 Quality Gate

Wiki 页面必须通过 6 级质量评估才能标记 active

---

## 12. 实现路径

### 当前已完成（代码现状）

| 模块 | 状态 |
|------|------|
| FastAPI 后端 + 26 个前端页面 | ✅ |
| 知识分类器（第一层 + 启发式兜底） | ✅ |
| Sync Ledger + 边界链接停止 | ✅ |
| URL 标准化（YouTube/B站/抖音/GitHub） | ✅ |
| Raw Sources 不可变存储 | ✅ |
| Wiki 页面生成（4 种类型） | ✅ |
| Query Agent（关键词检索） | ✅ |
| 抖音浏览器采集 + ASR 转写 | ✅ |
| 10 家 LLM 供应商适配 | ✅ |
| Quality Gate 质量评估 | ✅ |
| 回收站（30 天可恢复） | ✅ |
| Demo 体验层 | ✅ |
| 手动粘贴 + V3 统一输入 | ✅ |

### 待开发（优先级排序）

| 优先级 | 功能 | 工作量 |
|--------|------|--------|
| **P0** | Web Access CDP 采集（B站/抖音 eval 脚本） | 2-3 天 |
| **P0** | 第二层分类器（domain/topics/depth） | 0.5 天 |
| **P1** | ChromaDB 向量检索（替换关键词匹配） | 1-2 天 |
| **P1** | 知识图谱数据 + 可视化 | 2 天 |
| **P1** | Lint Agent | 1-2 天 |
| **P2** | 更多平台 CDP 脚本（小红书/YouTube/知乎） | 每平台 0.5 天 |
| **P2** | Activation Rules 定时调度引擎 | 0.5 天 |
| **P2** | Agent Legion 实际执行 | 1-2 天 |

### 未来迭代（不在本轮）

| 功能 | 说明 |
|------|------|
| Learning Planner Agent（学习路径日历） | 分析收藏趋势 → 按 depth 排序 → 分天计划 → 外部资源补充 |
| React SPA 重构 | 当前 Jinja2 SSR 够用，未来可升级 |
| WebSocket 实时流水线 | 需要前端配合 |
| PostgreSQL 迁移 | 生产级别时 |
| Obsidian 插件（输出通道） | Wiki 同步到本地笔记 |
| IM 机器人推送 | 微信/飞书每日推送 |
| 一键分享知识卡片 | Wiki → 精美图片 |

---

## 13. 演示策略

### 路演脚本（5 分钟）

**痛点（30s）：** "你的 B 站收藏夹有 500 条内容，知识和搞笑视频混在一起，全部沉睡。"

**解法（30s）：** "StarMind 自动同步、自动过滤、自动编译成个人知识 Wiki。你只管收藏，它帮你变成知识。"

**Live Demo（3min）：**
1. 展示已有 Wiki + 知识图谱
2. 演示 Web Access 采集：Agent 自动打开 B 站收藏页 → 后台提取 → 实时入库
3. 实时展示分类 → 提取 → Wiki 生成
4. Query Agent 问答（带来源引用）
5. 展示 Lint 发现的知识缺口

**技术+愿景（1min）：** 边界链接同步 + 不可变资料 + 10 家 LLM + 本地优先

### 保险措施

| 风险 | 兜底 |
|------|------|
| CDP/网络异常 | Mock Connector + 预置 Demo 数据 |
| 用户浏览器未开启 | 提示用户打开浏览器，或降级为手动粘贴 |
| LLM 响应慢 | 预计算 Wiki 页面 |
| 演示翻车 | 预录视频 fallback |

---

## 14. 风险矩阵

| 风险 | 概率 | 兜底 |
|------|------|------|
| 平台 DOM 变化 | 高 | eval 脚本可热更新 + Mock 数据 |
| CDP 浏览器未开启 | 中 | 提示用户打开浏览器 + 降级为手动粘贴 |
| LLM 分类错误 | 中 | confidence 阈值 + 启发式兜底 + 回收站可恢复 |
| LLM 成本 | 中 | DeepSeek 低成本 + 小模型分类/大模型编译 |
| 数据隐私 | 低 | 本地部署，所有操作在用户自己浏览器内 |

---

## 15. 项目目录结构（现有 + 新增）

```
starmind/
├── app/
│   ├── agent/               # Agent 框架（已实现）
│   │   ├── runner.py        # AgentRunner 编排
│   │   ├── tools.py         # KnowledgeSearchTool
│   │   ├── memory.py        # 对话记忆
│   │   ├── guardrails.py    # 安全边界
│   │   └── observability.py # JSONL trace
│   ├── connectors/          # 采集连接器
│   │   ├── base.py          # 基类
│   │   ├── douyin.py        # 抖音（已实现）
│   │   └── mock.py          # Mock（已实现）
│   ├── services/            # 业务服务层（已实现）
│   ├── llm/                 # LLM 抽象层（已实现）
│   ├── models/              # 数据模型（已实现）
│   ├── api/                 # API 路由（已实现）
│   └── main.py              # FastAPI 入口
├── extension/               # 🆕 Web Access CDP 采集脚本（待开发）
│   ├── bilibili_eval.js     # B站收藏页提取脚本
│   ├── douyin_eval.js       # 抖音收藏页提取脚本
│   └── common.js            # 通用工具函数
├── local_data/              # 运行时数据（.gitignore）
├── tests/
├── requirements.txt
└── README.md
```

---

## 16. 参考来源

- Andrej Karpathy, "LLM Wiki" GitHub Gist
- Chrome Extension Manifest V3 文档
- FastAPI 官方文档
- ECharts 力导向图

---

*StarMind PRD V2.0 — 2026-06-22 — "收藏即知识，StarMind 让你的收藏夹自己成长"*
