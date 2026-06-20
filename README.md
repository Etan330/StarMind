# StarMind

StarMind is a local-first open-source Agent framework for turning high-frequency saves into a self-maintaining personal knowledge system.

This MVP implements the local Agent foundation:

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

本轮重构方案的采纳评估见 `docs/refactor_adoption.md`。当前优先落地的是：状态词表、知识分类器、可恢复回收站，以及让用户只处理不确定内容的待处理流程。

## 本地运行

### 如何启动

如果你在 `/Users/etan330/Desktop/agent` 目录下：

```bash
cd starmind
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

如果你已经在 `starmind` 项目目录里：

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 如何打开

StarMind 默认运行在本地 `8000` 端口：

```text
http://127.0.0.1:8000
```

### 如何停止

如果服务是在当前终端里启动的，按：

```text
Control + C
```

如果服务在后台占用了 `8000` 端口，可以运行：

```bash
lsof -tiTCP:8000 -sTCP:LISTEN | xargs kill
```

## Use the MVP

1. Open `设置`.
2. Choose `DeepSeek`, select `deepseek-v4-flash`, paste your DeepSeek API Key, and save.
3. Optionally save the same settings as a named model profile. The profile appears in the home-page chat selector.
4. Open `连接来源` → `抖音`.
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

```bash
cd starmind
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
