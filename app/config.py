from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA_DIR = PROJECT_ROOT / "local_data"
CONFIG_DIR = LOCAL_DATA_DIR / "config"
BROWSER_DATA_DIR = LOCAL_DATA_DIR / "browser"
DATABASE_PATH = LOCAL_DATA_DIR / "starmind.db"
DATABASE_URL = os.getenv("STARMIND_DATABASE_URL", f"sqlite:///{DATABASE_PATH}")

# 豆包/点点 批量提取节奏节流（反爬：推迟人机验证出现）。
# 端点循环按此调度——每 N 条换新对话窗口，条间随机延时 [MIN, MAX] 秒。
# 默认值偏保守；请求体可传同名键覆盖以便实测调小。
DOUBAO_SWITCH_CONVO_EVERY = int(os.getenv("STARMIND_DOUBAO_SWITCH_EVERY", "2"))
DOUBAO_ITEM_DELAY_MIN = float(os.getenv("STARMIND_DOUBAO_DELAY_MIN", "15"))
DOUBAO_ITEM_DELAY_MAX = float(os.getenv("STARMIND_DOUBAO_DELAY_MAX", "40"))

DATA_DIRECTORIES = [
    LOCAL_DATA_DIR,
    LOCAL_DATA_DIR / "raw_sources",
    LOCAL_DATA_DIR / "source_summaries",
    LOCAL_DATA_DIR / "wiki",
    LOCAL_DATA_DIR / "sop",
    LOCAL_DATA_DIR / "methodology",
    LOCAL_DATA_DIR / "recycle_bin",
    LOCAL_DATA_DIR / "logs",
    BROWSER_DATA_DIR,
    CONFIG_DIR,
]

PROVIDERS_PATH = CONFIG_DIR / "providers.json"
MODEL_CONFIG_PATH = CONFIG_DIR / "model_config.json"
MODEL_PROFILES_PATH = CONFIG_DIR / "model_profiles.json"
SOURCE_CONNECTIONS_PATH = CONFIG_DIR / "source_connections.json"
ACTIVATION_RULES_PATH = CONFIG_DIR / "activation_rules.json"
AGENT_MEMORY_PATH = CONFIG_DIR / "agent_memory.json"
AGENT_TRACE_PATH = LOCAL_DATA_DIR / "logs" / "agent_runs.jsonl"
SECRETS_PATH = CONFIG_DIR / "secrets.json"
UI_PREFS_PATH = CONFIG_DIR / "ui_preferences.json"
WORKBENCH_LAYOUT_PATH = CONFIG_DIR / "workbench_layout.json"
DISTILL_REQUESTS_PATH = CONFIG_DIR / "distill_requests.json"
AGENT_LEGION_PATH = CONFIG_DIR / "agent_legion.json"


DEFAULT_MODEL_CONFIG: dict[str, Any] = {
    "default_provider": "deepseek",
    "default_model": "deepseek-v4-flash",
    "task_models": {
        "classifier_model": {"provider": "deepseek", "model": "deepseek-v4-flash"},
        "ingest_model": {"provider": "deepseek", "model": "deepseek-v4-flash"},
        "query_model": {"provider": "deepseek", "model": "deepseek-v4-flash"},
        "lint_model": {"provider": "deepseek", "model": "deepseek-v4-flash"},
        "repair_model": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    },
    "custom_model_names": {},
}

DEFAULT_PROVIDERS: dict[str, Any] = {
    "mock": {
        "display_name": "本地模拟模型",
        "api_style": "mock",
        "base_url": "local://mock",
        "models": ["mock-fast", "mock-smart"],
        "api_key_label": "无需 API Key",
        "adapter_status": "ready",
        "hidden": True,
    },
    "deepseek": {
        "display_name": "DeepSeek",
        "api_style": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
        "api_key_label": "DeepSeek API Key",
        "adapter_status": "ready",
    },
    "baidu_internal": {
        "display_name": "百度内部",
        "api_style": "openai_compatible",
        "base_url": "https://oneapi-comate.baidu-int.com/v1",
        "models": ["gpt-5.5"],
        "api_key_label": "百度内部 API Key",
        "adapter_status": "ready",
    },
    "openai": {
        "display_name": "OpenAI / ChatGPT",
        "api_style": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"],
        "api_key_label": "OpenAI API Key",
        "adapter_status": "ready",
    },
    "anthropic": {
        "display_name": "Anthropic / Claude",
        "api_style": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
        "api_key_label": "Anthropic API Key",
        "adapter_status": "ready",
    },
    "gemini": {
        "display_name": "Google / Gemini",
        "api_style": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "models": ["gemini-1.5-pro", "gemini-1.5-flash"],
        "api_key_label": "Gemini API Key",
        "adapter_status": "ready",
    },
    "glm": {
        "display_name": "GLM / 智谱",
        "api_style": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4", "glm-4-air", "glm-4-flash"],
        "api_key_label": "GLM API Key",
        "adapter_status": "ready",
    },
    "doubao": {
        "display_name": "豆包 / Doubao",
        "api_style": "openai_compatible",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-pro-32k", "doubao-lite-32k"],
        "api_key_label": "Doubao API Key",
        "adapter_status": "ready",
    },
    "minimax": {
        "display_name": "MiniMax",
        "api_style": "openai_compatible",
        "base_url": "https://api.minimax.io/v1",
        "models": ["MiniMax-M3", "MiniMax-M2.7"],
        "api_key_label": "MiniMax API Key",
        "adapter_status": "ready",
    },
    "kimi": {
        "display_name": "Kimi / Moonshot",
        "api_style": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "api_key_label": "Moonshot API Key",
        "adapter_status": "ready",
    },
    "qwen": {
        "display_name": "通义千问 / Qwen",
        "api_style": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
        "api_key_label": "DashScope API Key",
        "adapter_status": "ready",
    },
    "xiaomi_mimo": {
        "display_name": "小米 MIMO",
        "api_style": "openai_compatible",
        "base_url": "https://api.xiaomimimo.com/v1",
        "models": ["mimo-v2.5-pro", "mimo-v2.5"],
        "api_key_label": "MIMO API Key",
        "adapter_status": "ready",
    },
}

DEFAULT_MODEL_PROFILES: dict[str, Any] = {
    "active_profile_id": "",
    "profiles": [],
}

DEFAULT_UI_PREFS: dict[str, Any] = {
    "language": "zh",
}


DEFAULT_WORKBENCH_LAYOUT: dict[str, Any] = {
    "modules": [
        {"id": "today_sync", "position": 0, "size": "medium", "settings": {"show_counts": True}},
        {"id": "pending_items", "position": 1, "size": "medium", "settings": {"limit": 3}},
        {"id": "recent_sources", "position": 2, "size": "medium", "settings": {"limit": 3}},
        {"id": "knowledge_topics", "position": 3, "size": "medium", "settings": {"limit": 6}},
    ]
}

DEFAULT_DISTILL_REQUESTS: dict[str, Any] = {"requests": []}

DEFAULT_SOURCE_CONNECTIONS: dict[str, Any] = {"connections": {}}

DEFAULT_AGENT_MEMORY: dict[str, Any] = {"runs": [], "notes": []}

DEFAULT_ACTIVATION_RULES: dict[str, Any] = {
    "rules": [
        {
            "id": "daily_recall",
            "name": "每日知识唤醒",
            "trigger": "每天打开 StarMind 后",
            "cadence": "每天",
            "run_time": "09:30",
            "focus": "从最近收藏和长期未读资料里挑 3 条重新推送",
            "delivery": "首页提醒",
            "limit": 3,
            "status": "已启用",
        },
        {
            "id": "contextual_recall",
            "name": "上下文激活",
            "trigger": "用户提问或记录想法时",
            "cadence": "提问时",
            "run_time": "",
            "focus": "主动找出相关历史资料，提醒用户曾经收藏过什么",
            "delivery": "知识库侧栏",
            "limit": 5,
            "status": "已启用",
        },
        {
            "id": "weekly_distill",
            "name": "每周专项归纳",
            "trigger": "每周固定时间",
            "cadence": "每周",
            "run_time": "周日 20:00",
            "focus": "把近期收藏归纳成专题资料、SOP 或专项 Sub-Agent",
            "delivery": "待处理",
            "limit": 5,
            "status": "已启用",
        },
    ]
}

DEFAULT_AGENT_LEGION: dict[str, Any] = {
    "agents": [
        {
            "id": "methodology_researcher",
            "name": "方法论研究员",
            "focus": "把收藏内容归纳成可复用的方法论",
            "cadence": "每周",
        },
        {
            "id": "sop_architect",
            "name": "SOP 架构师",
            "focus": "把高频流程沉淀成专项 SOP",
            "cadence": "每两周",
        },
        {
            "id": "idea_curator",
            "name": "灵感整理员",
            "focus": "整理手动录入的想法与草稿",
            "cadence": "按需",
        },
    ]
}


def ensure_directories() -> None:
    for directory in DATA_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default.copy()
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def ensure_config_files() -> None:
    ensure_directories()
    if not PROVIDERS_PATH.exists():
        write_json(PROVIDERS_PATH, DEFAULT_PROVIDERS)
    if not MODEL_CONFIG_PATH.exists():
        write_json(MODEL_CONFIG_PATH, DEFAULT_MODEL_CONFIG)
    if not MODEL_PROFILES_PATH.exists():
        write_json(MODEL_PROFILES_PATH, DEFAULT_MODEL_PROFILES)
    if not SOURCE_CONNECTIONS_PATH.exists():
        write_json(SOURCE_CONNECTIONS_PATH, DEFAULT_SOURCE_CONNECTIONS)
    if not ACTIVATION_RULES_PATH.exists():
        write_json(ACTIVATION_RULES_PATH, DEFAULT_ACTIVATION_RULES)
    if not AGENT_MEMORY_PATH.exists():
        write_json(AGENT_MEMORY_PATH, DEFAULT_AGENT_MEMORY)
    if not SECRETS_PATH.exists():
        write_json(SECRETS_PATH, {"api_keys": {}})
    if not UI_PREFS_PATH.exists():
        write_json(UI_PREFS_PATH, DEFAULT_UI_PREFS)
    if not WORKBENCH_LAYOUT_PATH.exists():
        write_json(WORKBENCH_LAYOUT_PATH, DEFAULT_WORKBENCH_LAYOUT)
    if not DISTILL_REQUESTS_PATH.exists():
        write_json(DISTILL_REQUESTS_PATH, DEFAULT_DISTILL_REQUESTS)
    if not AGENT_LEGION_PATH.exists():
        write_json(AGENT_LEGION_PATH, DEFAULT_AGENT_LEGION)
