# StarMind

<details open>
<summary><strong>中文</strong></summary>

## StarMind 是什么

StarMind 是一个 local-first 的开源 Agent framework，用来把你高频保存的内容，逐步整理成一个可维护、可检索、可追问的个人知识系统。

当前 MVP 已经落地本地 Agent 基础能力：

- FastAPI 本地应用
- SQLite 数据库：`local_data/starmind.db`
- Raw Sources、Wiki、SOP、methodology、recycle bin、logs、config 等本地数据目录
- DeepSeek-first 的 provider/model 设置，以及本地 API key 存储
- 可保存 model profiles，并在首页知识问答中选择使用
- Agent framework：model runtime、instructions、tools、runner、memory、guardrails、JSONL observability
- MockProvider 与 provider abstraction
- OpenAI-compatible provider adapter
- Anthropic 与 Gemini minimal adapters
- MockConnector，内置 20 条模拟收藏内容
- Douyin browser collector scaffold，用于本地登录和可见收藏链接提取
- YouTube、Bilibili、Douyin、GitHub 与通用 URL normalization
- 基于 boundary-link 的 Sync Ledger stop logic
- Candidate Pool
- 从 candidate 生成 Raw Source 文件，并维护 Wiki 页面
- 本地 Web UI

本轮重构方案的采纳评估见 `docs/refactor_adoption.md`。当前优先落地的是：状态词表、知识分类器、可恢复回收站，以及让用户只处理不确定内容的待处理流程。

## 本地运行

### 新用户一键启动

新电脑拿到项目后，先进入项目目录，然后运行：

```bash
./start.sh
```

`start.sh` 会自动完成这些事情：

- 如果项目里还没有 `.venv`，自动创建 Python 虚拟环境。
- 安装或更新 `requirements.txt` 里的运行依赖。
- 启动本地服务：`http://127.0.0.1:8000`。

第一次启动需要联网下载依赖，后续再次运行同一个命令即可。项目要求 Python 3.9+；如果系统默认 `python3` 不可用，可以这样指定：

```bash
PYTHON=/path/to/python ./start.sh
```

### 手动启动

如果你不想使用一键脚本，也可以手动执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 打开 StarMind

StarMind 默认运行在本地 `8000` 端口：

```text
http://127.0.0.1:8000
```

### 停止服务

如果服务是在当前终端里启动的，按：

```text
Control + C
```

如果服务在后台占用了 `8000` 端口，可以运行：

```bash
lsof -tiTCP:8000 -sTCP:LISTEN | xargs kill
```

## 使用 MVP

1. 打开 `设置`。
2. 选择 `DeepSeek`，选择 `deepseek-v4-flash`，粘贴 DeepSeek API Key 并保存。
3. 你也可以把同一套设置保存为一个命名 model profile，之后可以在首页知识问答选择它。
4. 打开 `连接来源` -> `抖音`。
5. 点击 `打开抖音内置浏览器`，登录并进入 Douyin 收藏页。
6. 点击 `提取当前收藏页视频链接`，可见视频链接会被导入 `待处理`。
7. 打开 `待处理`，可以对单条内容点击 `生成原始资料和知识页`，也可以批量处理前 5 条。
8. 打开 `原始资料` 和 `知识库`，检查生成的文件和页面。
9. 在首页提问。StarMind 会先搜索本地 Raw Sources 和 Wiki pages，再调用当前选择的 model profile。

## 本地数据

所有运行时数据都保存在项目目录内：

```text
local_data/
  starmind.db
  raw_sources/
  source_summaries/
  wiki/
  sop/
  methodology/
  recycle_bin/
  logs/
    agent_runs.jsonl
  config/
    agent_memory.json
    model_config.json
    model_profiles.json
    providers.json
    secrets.json
```

`local_data/` 是 runtime-only 的用户数据，已被 `.gitignore` 忽略。它可能包含浏览器 session、提取的 sources、生成的 Wiki pages、logs、SQLite 数据，以及 `local_data/config/secrets.json` 中的 API keys。不要提交这个目录。

## Model Providers

Providers 定义在 `local_data/config/providers.json`。业务逻辑通过 `LLMProvider` abstraction 调用模型，而不是硬编码某个厂商。

MVP 支持的 adapters：

- `mock`
- `openai_compatible`
- `anthropic` minimal
- `gemini` minimal

DeepSeek 是默认真实 model provider。Kimi、GLM、Qwen、Doubao、MiniMax、Xiaomi MIMO、Baidu 等国内模型，在可行时通过 OpenAI-compatible metadata 配置。你可以在 `providers.json` 中编辑 placeholder providers，也可以在 UI 中添加 custom providers。

## Agent Runtime

本地 Agent path 被拆分为明确模块：

```text
app/agent/
  instructions.py
  guardrails.py
  tools.py
  runner.py
  memory.py
  observability.py
```

当前 runner 支持 knowledge-base question answering。Tools 会搜索本地 Wiki markdown 和 Raw Source text/transcripts。Guardrails 会阻止危险的本地文件、API key、cookie 操作。Memory 保存在 `local_data/config/agent_memory.json`，traces 追加写入 `local_data/logs/agent_runs.jsonl`。

## Douyin Collector

Douyin MVP 使用本地浏览器 session：

1. 在 `local_data/browser/douyin` 下打开一个 persistent Chrome session。
2. 让用户手动登录。
3. 从当前页面提取可见的 `douyin.com/video/...` links。
4. 将 links 导入正常的 Sync Ledger 和 Candidate flow。

这不会绕过登录、CAPTCHA、平台限制或私有访问控制。它只会在用户打开相关页面后读取可见链接。

## Boundary Sync Rules

StarMind 不使用 Wiki pages 判断某条内容是否已经扫描过。Wiki pages 是派生知识，可能会被合并或删除。

扫描会在 normalized item 已存在于以下任一位置时停止：

- Raw Sources
- Sync Ledger

Sync Ledger 会记录所有已扫描链接，包括后续被判断为 non-knowledge 的内容，所以它们不会被无限重复扫描。

## 测试

测试依赖不在默认运行依赖里。开发者需要先安装 `requirements-dev.txt`：

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

测试覆盖：

- canonical URLs 会移除 tracking parameters
- YouTube、Bilibili、Douyin、GitHub 会提取稳定 external IDs
- 第一次 MockConnector scan 会创建 candidates 和 ledger items
- 第二次 MockConnector scan 会在已有 boundary 上停止
- Douyin visible-link imports 会正确去重
- candidate 可以生成 Raw Source files 和 Wiki page

## 当前限制

这个 checkpoint 还没有执行真实 audio ASR。如果平台 transcript 或 page text 不可用，StarMind 会保存视频链接，并写入明确的 `audio_asr_pending` transcript placeholder。下一步 backend 计划是增加真实 ASR tool 和 background task queue。

Raw Sources 被建模为 immutable，且 `agent_delete_allowed = false`；未来只有用户主动删除流程可以移除它们。

</details>

<details>
<summary><strong>English</strong></summary>

## What Is StarMind

StarMind is a local-first open-source Agent framework for turning high-frequency saves into a self-maintaining personal knowledge system that can be searched, reviewed, and queried over time.

The current MVP implements the local Agent foundation:

- FastAPI local app
- SQLite database at `local_data/starmind.db`
- local data folders for Raw Sources, Wiki, SOP, methodology, recycle bin, logs, and config
- DeepSeek-first provider/model settings with local API key storage
- saved model profiles that can be selected from the home-page knowledge chat
- Agent framework: model runtime, instructions, tools, runner, memory, guardrails, and JSONL observability
- MockProvider and provider abstraction
- OpenAI-compatible provider adapter
- Anthropic and Gemini minimal adapters
- MockConnector with 20 simulated favorite items
- Douyin browser collector scaffold for local login and visible favorites extraction
- URL normalization for YouTube, Bilibili, Douyin, GitHub, and generic URLs
- Sync Ledger with boundary-link stop logic
- Candidate Pool
- Raw Source file generation and Wiki page maintenance from candidates
- local Web UI

See `docs/refactor_adoption.md` for the adoption assessment of the current refactor plan. The current priorities are the status vocabulary, knowledge classifier, recoverable recycle bin, and a pending-review flow where users only handle uncertain content.

## Local Run

### One-command Startup For New Users

After getting the project on a new machine, enter the project directory and run:

```bash
./start.sh
```

`start.sh` automatically handles the following:

- Creates the project `.venv` if it does not exist.
- Installs or updates runtime dependencies from `requirements.txt`.
- Starts the local service at `http://127.0.0.1:8000`.

The first startup needs network access to download dependencies. Later runs can reuse the same command. The project requires Python 3.9+. If the system default `python3` is unavailable, specify it explicitly:

```bash
PYTHON=/path/to/python ./start.sh
```

### Manual Startup

If you do not want to use the one-command script, run the steps manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Open StarMind

StarMind runs on local port `8000` by default:

```text
http://127.0.0.1:8000
```

### Stop The Service

If the service is running in the current terminal, press:

```text
Control + C
```

If another background process is occupying port `8000`, run:

```bash
lsof -tiTCP:8000 -sTCP:LISTEN | xargs kill
```

## Use The MVP

1. Open `设置`.
2. Choose `DeepSeek`, select `deepseek-v4-flash`, paste your DeepSeek API Key, and save.
3. Optionally save the same settings as a named model profile. The profile appears in the home-page chat selector.
4. Open `连接来源` -> `抖音`.
5. Click `打开抖音内置浏览器`, log in, and navigate to the Douyin favorites page.
6. Click `提取当前收藏页视频链接`. Visible video links are imported into `待处理`.
7. Open `待处理` and click `生成原始资料和知识页` for one item, or `处理前 5 条` for a batch.
8. Open `原始资料` and `知识库` to inspect generated files and pages.
9. Ask a question from the home page. StarMind searches local Raw Sources and Wiki pages before calling the selected model profile.

## Local Data

All local runtime data stays inside this project:

```text
local_data/
  starmind.db
  raw_sources/
  source_summaries/
  wiki/
  sop/
  methodology/
  recycle_bin/
  logs/
    agent_runs.jsonl
  config/
    agent_memory.json
    model_config.json
    model_profiles.json
    providers.json
    secrets.json
```

`local_data/` is runtime-only user data and is ignored by `.gitignore`. It may contain browser sessions, extracted sources, generated Wiki pages, logs, SQLite data, and `local_data/config/secrets.json` API keys. Do not commit it.

## Model Providers

Providers are defined in `local_data/config/providers.json`. Business logic uses the `LLMProvider` abstraction instead of hard-coding a vendor.

Supported MVP adapters:

- `mock`
- `openai_compatible`
- `anthropic` minimal
- `gemini` minimal

DeepSeek is the default real model provider. Domestic providers such as Kimi, GLM, Qwen, Doubao, MiniMax, Xiaomi MIMO, and Baidu are configured through OpenAI-compatible metadata when possible. Placeholder providers can be edited in `providers.json` or added from the UI as custom providers.

## Agent Runtime

The local Agent path is split into explicit modules:

```text
app/agent/
  instructions.py
  guardrails.py
  tools.py
  runner.py
  memory.py
  observability.py
```

The runner currently supports knowledge-base question answering. Tools search local Wiki markdown and Raw Source text/transcripts. Guardrails block dangerous local-file/API-key/cookie operations. Memory is stored in `local_data/config/agent_memory.json`, and traces are appended to `local_data/logs/agent_runs.jsonl`.

## Douyin Collector

The Douyin MVP uses a local browser session:

1. Open a persistent Chrome session under `local_data/browser/douyin`.
2. Let the user log in manually.
3. Extract visible `douyin.com/video/...` links from the current page.
4. Import links into the normal Sync Ledger and Candidate flow.

This does not bypass login, CAPTCHA, platform limits, or private access controls. It only reads visible links after the user opens the relevant page.

## Boundary Sync Rules

StarMind does not use Wiki pages to decide whether an item was scanned. Wiki pages are derived knowledge and may be merged or deleted.

The scan stops when a normalized item already exists in either:

- Raw Sources
- Sync Ledger

Sync Ledger records all scanned links, including items that later become non-knowledge, so they are not re-scanned forever.

## Tests

Test dependencies are not included in the default runtime dependencies. Developers should install `requirements-dev.txt` first:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

The tests verify:

- tracking parameters are removed from canonical URLs
- stable external IDs are extracted for YouTube, Bilibili, Douyin, and GitHub
- first MockConnector scan creates candidates and ledger items
- second MockConnector scan stops on the existing boundary
- Douyin visible-link imports dedupe correctly
- a candidate can generate Raw Source files and a Wiki page

## Current Limits

This checkpoint does not yet perform real audio ASR. If a platform transcript or page text is unavailable, StarMind stores the video link and writes a clear `audio_asr_pending` transcript placeholder. The next backend step is to add a real ASR tool and a background task queue.

Raw Sources are modeled as immutable and `agent_delete_allowed = false`; only future user-initiated delete flows should remove them.

</details>
