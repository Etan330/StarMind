# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Activate virtualenv (Python 3.14 venv at .venv)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Run all tests
pytest

# Run a single test file or function
pytest tests/test_sync_service.py
pytest tests/test_url_normalizer.py::test_youtube_tracking_stripped -v
```

No linter config is committed; use `ruff` if needed. Playwright must be installed for Douyin browser tests (`playwright install chromium`).

## Architecture

StarMind is a **local-first personal knowledge agent** — it syncs social-media favorites (Douyin, Bilibili, YouTube, etc.), classifies them, generates Raw Sources + Wiki pages, and answers questions over the accumulated knowledge base.

### Core layers

```
app/
  main.py            → FastAPI factory (create_app), mounts static + router
  config.py          → All paths, JSON defaults, read_json/write_json helpers
  database.py        → SQLAlchemy (SQLite), Base, SessionLocal, init_db()
  models/records.py  → All ORM models (Connector, SyncLedgerItem, CandidateItem,
                       RawSource, WikiPage, RecycleBinItem, KnowledgeClassification, …)
  api/routes.py      → Single large router: HTML UI pages + JSON API endpoints
  templates/         → Jinja2 HTML (base.html + per-page)
  static/css/        → CSS

  services/
    sync_service.py         → SyncService — connector scanning, import_items, boundary-stop logic
    url_normalizer.py       → normalize_url() — canonical URLs + external_item_id extraction
    classifier_service.py   → ClassifierService — LLM-based knowledge/non-knowledge classification
    raw_source_service.py   → RawSourceService — ingest_candidate → RawSource creation
    wiki_service.py         → WikiMaintenanceService — LLM page generation from raw source
    recycle_service.py      → RecycleService — soft-delete with 30-day expiry
    quality_service.py      → Page quality scoring
    statuses.py             → Canonical status constants (PENDING_CLASSIFICATION, INGESTED, etc.)

  llm/
    providers.py    → LLMProvider ABC, MockProvider, OpenAICompatibleProvider, Anthropic/Gemini adapters
    registry.py     → Provider resolution: get_provider_runtime(), settings CRUD, API key storage

  agent/
    runner.py       → AgentRunner.answer_question() — search local KB then call LLM
    tools.py        → KnowledgeSearchTool — searches Wiki markdown + Raw Source text
    instructions.py → System/query prompts
    guardrails.py   → Input validation (blocks dangerous ops)
    memory.py       → Agent memory (local_data/config/agent_memory.json)
    observability.py→ JSONL trace logging

  connectors/
    base.py         → BaseConnector ABC, ConnectorItem dataclass
    mock.py         → MockConnector (20 simulated items)
    douyin.py       → DouyinBrowserCollector — Playwright persistent session
```

### Data flow

1. **Connector scan / manual import** → `SyncService` creates `SyncLedgerItem` + `CandidateItem` (status: `pending_classification`)
2. **Classification** → `ClassifierService` calls LLM → marks candidate as knowledge / non-knowledge / uncertain
3. **Ingestion** → `RawSourceService.ingest_candidate()` → creates `RawSource` record + files under `local_data/raw_sources/`
4. **Wiki generation** → `WikiMaintenanceService.create_page_from_raw_source()` → LLM generates markdown → `WikiPage` (status: `needs_review`)
5. **User review** → confirms page → status becomes `active`
6. **Q&A** → `AgentRunner` searches KB with `KnowledgeSearchTool`, calls LLM with context

### Key design decisions

- **Boundary-stop sync**: scan stops when `canonical_url` or `external_item_id` already exists in RawSource or SyncLedger — Wiki pages are NOT used for dedup.
- **RawSource immutability**: `agent_delete_allowed = false`; only user-initiated deletion is allowed.
- **Provider abstraction**: all LLM calls go through `LLMProvider.chat()` / `json_chat()`; DeepSeek is the default real provider; most Chinese providers use `openai_compatible` adapter.
- **All runtime data** lives in `local_data/` (gitignored). Config JSONs are auto-created on first boot via `ensure_config_files()`.
- **Tests** use in-memory SQLite (`sqlite:///:memory:` with `StaticPool`). No test DB fixtures needed.

## Conventions

- Python ≥ 3.9 style with `from __future__ import annotations`.
- Pydantic v2 for request schemas (`app/schemas/`), SQLAlchemy 2.0 mapped columns for ORM.
- Routes serve both HTML (Jinja2 template) and JSON based on `Accept` header (`wants_html()`). POST endpoints redirect on HTML, return JSON otherwise.
- Status constants live in `app/services/statuses.py` — always import from there.
- URL normalization (stripping tracking params, extracting platform external IDs) is in `url_normalizer.py`.
