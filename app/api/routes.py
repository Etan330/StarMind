from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agent import AgentRunner
from app.connectors import BrowserDependencyMissing, CDPConnectionError, DouyinPageNotReady, cdp_proxy, douyin_browser_collector
from app.connectors.base import ConnectorItem
from app.config import (
    AGENT_LEGION_PATH,
    ACTIVATION_RULES_PATH,
    DEFAULT_ACTIVATION_RULES,
    DEFAULT_AGENT_LEGION,
    DEFAULT_DISTILL_REQUESTS,
    DEFAULT_MODEL_PROFILES,
    DEFAULT_SOURCE_CONNECTIONS,
    DEFAULT_UI_PREFS,
    DEFAULT_WORKBENCH_LAYOUT,
    DISTILL_REQUESTS_PATH,
    LOCAL_DATA_DIR,
    PROJECT_ROOT,
    MODEL_PROFILES_PATH,
    SOURCE_CONNECTIONS_PATH,
    UI_PREFS_PATH,
    WORKBENCH_LAYOUT_PATH,
    DOUBAO_SWITCH_CONVO_EVERY,
    DOUBAO_ITEM_DELAY_MIN,
    DOUBAO_ITEM_DELAY_MAX,
    read_json,
    write_json,
)
from app.database import get_db
from app.llm import (
    add_custom_provider,
    clear_api_key,
    get_model_settings,
    get_providers,
    save_model_settings,
    save_provider_api_key,
    save_provider_base_url,
    test_active_connection,
    test_model_connection,
)
from app.models import CandidateItem, ChatConversation, ChatMessage, Connector, KnowledgeGraphEdge, KnowledgeClassification, PushSettings, RawSource, RecycleBinItem, ScanEntry, ScanLog, SyncLedgerItem, WikiPage
from app.services import (
    ClassifierService,
    RawSourceService,
    RecycleService,
    ScanEntryService,
    SyncService,
    TrackingService,
    V3_ENTRY_MODES,
    WikiMaintenanceService,
    classify_v3_input,
    compute_page_quality,
    generation_label,
    get_demo_result,
    get_v3_home_preview,
    list_demo_results,
    markdown_key_points,
    markdown_summary,
    quality_label,
    suggested_questions,
    transcript_label,
    normalize_url,
)
from app.services.markdown_renderer import render_markdown
from app.services.douyin_transcript_service import DouyinTranscriptError, DouyinTranscriptService
from app.services.statuses import (
    ARCHIVED_RECOVERABLE,
    CLASSIFIED_KNOWLEDGE,
    CLASSIFIED_UNCERTAIN,
    INGESTED,
    PENDING_CLASSIFICATION,
    RECYCLE_STATUSES,
    RECYCLED,
    REVIEWABLE_STATUSES,
    SKIPPED,
)


router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))
BROWSER_SESSIONS: dict[str, dict[str, Any]] = {}
# 豆包/点点 批量提取的会话态（内存级，仿 BROWSER_SESSIONS）。
# key=job_id；记录本次批次进度，用于人机验证暂停后的断点续跑聚合计数。
# 持久断点靠 candidate.metadata_json 的 *_extracted 标记（重启后仍可续跑，只丢聚合计数）。
DOUBAO_EXTRACT_JOBS: dict[str, dict[str, Any]] = {}

V3_UI_EVENT_NAMES = {
    "v3_primary_input_focused",
    "v3_entry_clicked",
    "v3_onboarding_completed",
    "v3_demo_used",
}

WORKBENCH_MODULES: list[dict[str, str]] = [
    {"id": "today_sync", "name": "今天要消化", "description": "把新同步的收藏先处理掉"},
    {"id": "pending_items", "name": "待确认收藏", "description": "少量需要你判断的边界内容"},
    {"id": "recent_sources", "name": "最近入库", "description": "已经沉淀为原始资料的收藏"},
    {"id": "knowledge_topics", "name": "正在长出的主题", "description": "收藏里反复出现的知识主题"},
]

NAV_LABELS = {
    "zh": {
        "home": "首页",
        "create": "创建任务",
        "workbench": "工作台",
        "connectors": "连接来源",
        "pending": "待处理",
        "sources": "原始资料",
        "wiki": "知识库",
        "history": "历史记录",
        "activation": "激活",
        "settings": "设置",
        "guide": "帮助",
    },
    "en": {
        "home": "Home",
        "create": "Create Task",
        "workbench": "Workbench",
        "connectors": "Sources",
        "pending": "Review",
        "sources": "Raw Data",
        "wiki": "Knowledge",
        "history": "History",
        "activation": "Recall",
        "settings": "Settings",
        "guide": "Guide",
    },
}

HOME_COPY = {
    "zh": {
        "title": "输入信息，沉淀成可追问的知识。",
        "subtitle": "同步收藏夹、粘贴链接、输入博主或记录灵感，StarMind 会帮你提炼摘要、关键观点、来源证据和下一步问题。",
        "eyebrow": "AI 信息蒸馏工作台",
        "sync": "同步抖音收藏",
        "link": "导入链接",
        "idea": "记录想法",
        "ask": "向知识库提问",
        "ask_placeholder": "问问你的知识库，比如：最近收藏里关于 Agent 的核心观点是什么？",
        "send": "提问",
        "console_search": "询问你的知识库...",
        "console_raw": "原始资料",
        "console_raw_note": "已同步 10 条",
        "console_page": "知识页面",
        "console_page_note": "个人方法论",
        "console_signal": "知识激活",
        "console_signal_note": "主动提醒可用内容",
    },
    "en": {
        "title": "Input information. Distill it into queryable knowledge.",
        "subtitle": "Sync saves, paste links, enter a creator profile, or capture an idea. StarMind extracts summaries, key points, source evidence, and follow-up questions.",
        "eyebrow": "AI information distillation workspace",
        "sync": "Sync Douyin",
        "link": "Import link",
        "idea": "Capture idea",
        "ask": "Ask your knowledge base",
        "ask_placeholder": "Ask something like: what are the key Agent ideas in my recent saves?",
        "send": "Ask",
        "console_search": "Ask your knowledge base...",
        "console_raw": "Raw sources",
        "console_raw_note": "10 items synced",
        "console_page": "Knowledge page",
        "console_page_note": "Personal methods",
        "console_signal": "Knowledge recall",
        "console_signal_note": "Bring useful notes back",
    },
}

PLATFORM_PRESETS: list[dict[str, str | int]] = [
    {
        "name": "抖音",
        "platform": "douyin",
        "status": "开发中",
        "action": "配置收藏夹",
        "logo_url": "https://cdn.simpleicons.org/tiktok/000000",
        "priority": 1,
        "fit": "高优先级",
        "reason": "国内短视频高频收藏平台，适合蒸馏博主与收藏夹内容。",
        "auth_hint": "Cookie / 本地浏览器会话",
    },
    {
        "name": "TikTok",
        "platform": "tiktok",
        "status": "开发中",
        "action": "配置收藏夹",
        "logo_url": "https://cdn.simpleicons.org/tiktok/FFFFFF",
        "priority": 2,
        "fit": "高优先级",
        "reason": "海外短视频高频平台，用户收藏和关注列表价值高。",
        "auth_hint": "Cookie / 本地浏览器会话",
    },
    {
        "name": "小红书",
        "platform": "xiaohongshu",
        "status": "未连接",
        "action": "连接账号",
        "logo_url": "https://cdn.simpleicons.org/xiaohongshu/FF2442",
        "priority": 3,
        "fit": "高优先级",
        "reason": "适合沉淀经验贴、清单、教程和创作者主页内容；当前先按本地浏览器会话保存接入信息。",
        "auth_hint": "Cookie / 本地浏览器会话 / 收藏页链接",
    },
    {
        "name": "Bilibili",
        "platform": "bilibili",
        "status": "未连接",
        "action": "连接收藏夹",
        "logo_url": "https://cdn.simpleicons.org/bilibili/00A1D6",
        "priority": 4,
        "fit": "高优先级",
        "reason": "知识视频和稍后再看内容丰富，适合转成原始资料与方法论。",
        "auth_hint": "Cookie / 公开视频 API",
    },
    {
        "name": "YouTube",
        "platform": "youtube",
        "status": "未连接",
        "action": "连接收藏夹",
        "logo_url": "https://cdn.simpleicons.org/youtube/FF0000",
        "priority": 5,
        "fit": "高优先级",
        "reason": "海外长视频知识源，收藏列表与频道主页都适合后续蒸馏。",
        "auth_hint": "API Key / OAuth",
    },
    {
        "name": "知乎",
        "platform": "zhihu",
        "status": "开发中",
        "action": "配置收藏夹",
        "logo_url": "https://cdn.simpleicons.org/zhihu/0084FF",
        "priority": 6,
        "fit": "高优先级",
        "reason": "问答、专栏和收藏夹知识密度高，适合结构化沉淀。",
        "auth_hint": "Cookie / 收藏夹链接",
    },
    {
        "name": "微博",
        "platform": "weibo",
        "status": "开发中",
        "action": "配置收藏夹",
        "logo_url": "https://cdn.simpleicons.org/sinaweibo/E6162D",
        "priority": 7,
        "fit": "中优先级",
        "reason": "适合跟踪博主、话题和碎片观点，但噪声比知识视频更高。",
        "auth_hint": "Cookie / 用户主页",
    },
    {
        "name": "Twitter / X",
        "platform": "twitter",
        "status": "开发中",
        "action": "配置收藏夹",
        "logo_url": "https://cdn.simpleicons.org/x/FFFFFF",
        "priority": 8,
        "fit": "中优先级",
        "reason": "适合收集行业观点、线程和作者主页，API 成本与权限需后续处理。",
        "auth_hint": "OAuth / API Key",
    },
    {
        "name": "Instagram",
        "platform": "instagram",
        "status": "开发中",
        "action": "配置收藏夹",
        "logo_url": "https://cdn.simpleicons.org/instagram/E4405F",
        "priority": 9,
        "fit": "中优先级",
        "reason": "收藏和创作者主页适合灵感类资料，文本抽取能力需单独增强。",
        "auth_hint": "Cookie / Graph API",
    },
    {
        "name": "Facebook",
        "platform": "facebook",
        "status": "开发中",
        "action": "配置收藏夹",
        "logo_url": "https://cdn.simpleicons.org/facebook/1877F2",
        "priority": 10,
        "fit": "中优先级",
        "reason": "适合海外公开主页和收藏内容，但个人数据权限复杂。",
        "auth_hint": "OAuth / Graph API",
    },
    {
        "name": "Reddit",
        "platform": "reddit",
        "status": "未连接",
        "action": "连接收藏夹",
        "logo_url": "https://cdn.simpleicons.org/reddit/FF4500",
        "priority": 11,
        "fit": "中优先级",
        "reason": "讨论串和用户主页适合观点蒸馏，噪声需要分类器处理。",
        "auth_hint": "API Key / 用户名",
    },
    {
        "name": "GitHub",
        "platform": "github",
        "status": "未连接",
        "action": "连接收藏夹",
        "logo_url": "https://cdn.simpleicons.org/github/FFFFFF",
        "priority": 12,
        "fit": "中优先级",
        "reason": "Stars、仓库 README 和 Issues 适合技术知识库沉淀。",
        "auth_hint": "Personal Access Token",
    },
    {
        "name": "微信读书",
        "platform": "weread",
        "status": "开发中",
        "action": "配置划线",
        "logo_url": "",
        "priority": 13,
        "fit": "中优先级",
        "reason": "读书划线和笔记质量高，但接入方式依赖本地 Cookie。",
        "auth_hint": "Cookie / 导出文件",
    },
    {
        "name": "Pocket",
        "platform": "pocket",
        "status": "开发中",
        "action": "配置稍后读",
        "logo_url": "https://cdn.simpleicons.org/pocket/EF3F56",
        "priority": 14,
        "fit": "补充来源",
        "reason": "典型稍后读工具，适合补齐网页收藏场景。",
        "auth_hint": "OAuth",
    },
]

PLATFORM_EXCLUSIONS: list[dict[str, str]] = [
    {"name": "微信聊天 / Telegram 私聊", "reason": "隐私边界重，默认不作为 MVP 收藏夹来源。"},
    {"name": "电商 / 外卖平台", "reason": "收藏信息多为消费决策，知识密度不稳定，后续按垂直场景单独评估。"},
    {"name": "纯音乐平台", "reason": "收藏对象通常不是文本知识，暂不进入知识库主链路。"},
]

SOCIAL_FAVORITE_PLATFORMS = {
    "douyin",
    "tiktok",
    "xiaohongshu",
    "bilibili",
    "youtube",
    "zhihu",
    "weibo",
    "twitter",
    "instagram",
    "facebook",
    "reddit",
}

FAVORITE_PLATFORM_CAPABILITIES: dict[str, dict[str, str]] = {
    "douyin": {
        "status_label": "已支持同步",
        "status_tone": "success",
        "support_level": "live",
        "capability": "本地浏览器登录后，可提取当前收藏 / 喜欢页面可见的视频链接，并进入待处理流程。",
        "workflow": "打开浏览器 → 登录抖音 → 进入收藏页 → 提取并处理可见收藏",
    },
    "xiaohongshu": {
        "status_label": "可执行预筛选",
        "status_tone": "success",
        "support_level": "live",
        "capability": "本地浏览器登录后，可自动尝试进入收藏页并扫描可见笔记标题。",
        "workflow": "打开官网 → 登录小红书 → 自动进入收藏页 → 扫描标题并预筛选",
    },
    "bilibili": {
        "status_label": "可执行预筛选",
        "status_tone": "success",
        "support_level": "live",
        "capability": "本地浏览器登录后，可自动尝试进入收藏页并扫描可见视频标题。",
        "workflow": "打开官网 → 登录 B站 → 自动进入收藏页 → 扫描标题并预筛选",
    },
}


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


async def request_data(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return await request.json()
    form = await request.form()
    return dict(form)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def connector_to_dict(connector: Connector) -> dict[str, Any]:
    return {
        "id": connector.id,
        "name": connector.name,
        "platform": connector.platform,
        "connector_type": connector.connector_type,
        "status": connector.status,
        "auth_method": connector.auth_method,
        "last_successful_scan_at": iso(connector.last_successful_scan_at),
        "last_boundary_url": connector.last_boundary_url,
        "last_boundary_external_id": connector.last_boundary_external_id,
        "last_top_url": connector.last_top_url,
        "scan_mode": connector.scan_mode,
        "max_scan_pages": connector.max_scan_pages,
        "created_at": iso(connector.created_at),
        "updated_at": iso(connector.updated_at),
    }


def candidate_to_dict(candidate: CandidateItem) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "source_type": candidate.source_type,
        "platform": candidate.platform,
        "connector_id": candidate.connector_id,
        "external_item_id": candidate.external_item_id,
        "canonical_url": candidate.canonical_url,
        "raw_url": candidate.raw_url,
        "title": candidate.title,
        "author": candidate.author,
        "content_type": candidate.content_type,
        "metadata": json.loads(candidate.metadata_json or "{}"),
        "status": candidate.status,
        "created_at": iso(candidate.created_at),
        "updated_at": iso(candidate.updated_at),
    }


def dashboard_stats(db: Session) -> dict[str, Any]:
    pending_count = db.query(CandidateItem).filter(CandidateItem.status.in_(REVIEWABLE_STATUSES)).count()
    skipped_count = db.query(CandidateItem).filter(CandidateItem.status == SKIPPED).count()
    raw_source_count = db.query(RawSource).count()
    recycle_bin_count = db.query(RecycleBinItem).count()
    intake_total = pending_count + raw_source_count + recycle_bin_count
    return {
        "connector_count": db.query(Connector).count(),
        "candidate_count": db.query(CandidateItem).count(),
        "pending_count": pending_count,
        "skipped_count": skipped_count,
        "raw_source_count": raw_source_count,
        "wiki_page_count": db.query(WikiPage).count(),
        "recycle_bin_count": recycle_bin_count,
        "intake_total": intake_total,
        "ledger_count": db.query(SyncLedgerItem).count(),
        "last_scan": db.query(ScanLog).order_by(ScanLog.created_at.desc()).first(),
    }


def workbench_stats_payload(stats: dict[str, Any]) -> dict[str, int]:
    return {
        "pending_count": int(stats.get("pending_count") or 0),
        "raw_source_count": int(stats.get("raw_source_count") or 0),
        "recycle_bin_count": int(stats.get("recycle_bin_count") or 0),
        "intake_total": int(stats.get("intake_total") or 0),
    }


def conversation_payload(conversation: ChatConversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": iso(conversation.created_at),
        "updated_at": iso(conversation.updated_at),
    }


def message_payload(message: ChatMessage) -> dict[str, Any]:
    sources = json.loads(message.sources_json or "[]")
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "content_html": render_markdown(message.content) if message.role == "assistant" else "",
        "sources": sources,
        "created_at": iso(message.created_at),
    }


def template_context(request: Request, active: str, db: Session, **extra: Any) -> dict[str, Any]:
    ui_prefs = get_ui_prefs()
    language = ui_prefs["language"]
    context = {
        "request": request,
        "active": active,
        "stats": dashboard_stats(db),
        "model_settings": get_model_settings(),
        "providers": get_providers(),
        "ui_lang": language,
        "nav_labels": NAV_LABELS[language],
        "home_copy": HOME_COPY[language],
    }
    context.update(extra)
    return context


def get_workbench_layout() -> dict[str, Any]:
    layout = read_json(WORKBENCH_LAYOUT_PATH, DEFAULT_WORKBENCH_LAYOUT)
    layout.setdefault("modules", DEFAULT_WORKBENCH_LAYOUT["modules"])
    return layout


def get_distill_requests() -> dict[str, Any]:
    payload = read_json(DISTILL_REQUESTS_PATH, DEFAULT_DISTILL_REQUESTS)
    payload.setdefault("requests", [])
    return payload


def get_agent_legion() -> dict[str, Any]:
    payload = read_json(AGENT_LEGION_PATH, DEFAULT_AGENT_LEGION)
    payload.setdefault("agents", DEFAULT_AGENT_LEGION["agents"])
    return payload


def get_model_profiles() -> dict[str, Any]:
    payload = read_json(MODEL_PROFILES_PATH, DEFAULT_MODEL_PROFILES)
    payload.setdefault("active_profile_id", DEFAULT_MODEL_PROFILES.get("active_profile_id", ""))
    payload.setdefault("profiles", DEFAULT_MODEL_PROFILES["profiles"])
    return payload


def get_ui_prefs() -> dict[str, Any]:
    payload = read_json(UI_PREFS_PATH, DEFAULT_UI_PREFS)
    language = payload.get("language", "zh")
    if language not in {"zh", "en"}:
        language = "zh"
    payload["language"] = language
    return payload


def get_source_connections() -> dict[str, Any]:
    payload = read_json(SOURCE_CONNECTIONS_PATH, DEFAULT_SOURCE_CONNECTIONS)
    payload.setdefault("connections", {})
    return payload


PLATFORM_BROWSER_ENTRY_URLS = {
    "bilibili": "https://www.bilibili.com",
    "xiaohongshu": "https://www.xiaohongshu.com/explore",
}

PLATFORM_DEFAULT_FAVORITES_URLS = {
}

PLATFORM_OPEN_LABELS = {
    "douyin": "打开抖音官网登录",
    "bilibili": "打开 B站官网登录",
    "xiaohongshu": "打开小红书官网登录",
}


def saved_source_homepage_url(platform: str) -> str:
    connection = get_source_connections().get("connections", {}).get(platform, {})
    return str(connection.get("homepage_url") or "").strip() if isinstance(connection, dict) else ""


def is_platform_favorites_url(platform: str, url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)
    if platform == "bilibili":
        return host == "space.bilibili.com" and path.endswith("/favlist") and bool(query.get("fid"))
    if platform == "xiaohongshu":
        return host == "www.xiaohongshu.com" and "/user/profile/" in path and "fav" in query.get("tab", [])
    return False


def resolve_platform_favorites_url(platform: str, request_url: str = "") -> str:
    requested = str(request_url or "").strip()
    if requested:
        return requested
    saved = saved_source_homepage_url(platform)
    if saved:
        return saved
    default_url = PLATFORM_DEFAULT_FAVORITES_URLS.get(platform, "")
    if default_url:
        return default_url
    raise HTTPException(
        status_code=428,
        detail={
            "code": "user_favorites_url_required",
            "message": f"{platform} 收藏页绑定你的账号/收藏夹 ID，无法使用通用链接。请先点击“打开官网登录”，进入真实收藏页后保存该页面链接，再扫描标题。",
        },
    )


async def open_platform_browser(platform: str, request_url: str = "") -> dict[str, str]:
    entry_url = PLATFORM_BROWSER_ENTRY_URLS.get(platform)
    if not entry_url:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    favorites_url = str(request_url or "").strip() or saved_source_homepage_url(platform)
    await cdp_proxy.connect()
    tab = await cdp_proxy.new_tab(entry_url)
    if favorites_url and favorites_url != entry_url:
        await cdp_proxy.navigate(tab, favorites_url)
        tab.url = favorites_url
    await cdp_proxy.wait_for_load(tab)
    info = await cdp_proxy.get_info(tab)
    return {"current_url": str(info.get("url") or tab.url or favorites_url or entry_url)}


def get_activation_rules() -> dict[str, Any]:
    payload = read_json(ACTIVATION_RULES_PATH, DEFAULT_ACTIVATION_RULES)
    payload.setdefault("rules", DEFAULT_ACTIVATION_RULES["rules"])
    return payload


def get_active_profile_id(settings: dict[str, Any], payload: dict[str, Any]) -> str:
    active_profile_id = str(payload.get("active_profile_id") or "")
    profile = next((item for item in payload.get("profiles", []) if item.get("id") == active_profile_id), None)
    if not profile:
        return ""
    if profile.get("provider") != settings.get("default_provider"):
        return ""
    if profile.get("model") != settings.get("default_model"):
        return ""
    return active_profile_id


def favorite_platform_sort_key(item: dict[str, str | int]) -> tuple[int, int]:
    platform = str(item["platform"])
    if platform == "douyin":
        return (-1, int(item["priority"]))
    if platform == "tiktok":
        return (1, int(item["priority"]))
    return (0, int(item["priority"]))


def favorite_platform_cards(db: Session) -> list[dict[str, Any]]:
    source_connections = get_source_connections()["connections"]
    connectors = {connector.platform: connector for connector in db.query(Connector).all()}
    cards: list[dict[str, Any]] = []
    for preset in sorted(PLATFORM_PRESETS, key=favorite_platform_sort_key):
        platform = str(preset["platform"])
        if platform not in SOCIAL_FAVORITE_PLATFORMS:
            continue
        connection = source_connections.get(platform, {})
        connector = connectors.get(platform)
        capability = FAVORITE_PLATFORM_CAPABILITIES.get(
            platform,
            {
                "status_label": "待接入",
                "status_tone": "neutral",
                "support_level": "planned",
                "capability": "当前先展示接入说明和本地配置入口，真实收藏页解析器将在后续版本接入。",
                "workflow": "保存接入信息 → 等待平台解析器接入",
            },
        )
        if connection and capability["support_level"] == "planned":
            status_label = "已保存配置"
            status_tone = "neutral"
        else:
            status_label = capability["status_label"]
            status_tone = capability["status_tone"]
        cards.append(
            {
                "platform": platform,
                "name": preset["name"],
                "logo_url": preset.get("logo_url", ""),
                "reason": preset["reason"],
                "auth_hint": preset["auth_hint"],
                "status_label": status_label,
                "status_tone": status_tone,
                "support_level": capability["support_level"],
                "capability": capability["capability"],
                "workflow": capability["workflow"],
                "is_configured": bool(connection),
                "connector_status": connector.status if connector else "未配置",
                "last_top_url": connector.last_top_url if connector else "",
                "manage_url": f"/ui/source-setup/{platform}",
            }
        )
    return cards


WIKI_SECTIONS = [
    {"id": "knowledge", "name": "知识主题", "page_type": "knowledge"},
    {"id": "methodology", "name": "方法论", "page_type": "methodology"},
    {"id": "sop", "name": "SOP", "page_type": "sop"},
]


def latest_classifications(db: Session, candidate_ids: list[int]) -> dict[int, KnowledgeClassification]:
    if not candidate_ids:
        return {}
    rows = (
        db.query(KnowledgeClassification)
        .filter(KnowledgeClassification.candidate_id.in_(candidate_ids))
        .order_by(KnowledgeClassification.created_at.desc())
        .all()
    )
    latest: dict[int, KnowledgeClassification] = {}
    for row in rows:
        latest.setdefault(row.candidate_id, row)
    return latest


def read_wiki_markdown(page: WikiPage | None) -> str:
    if not page:
        return ""
    path = Path(page.markdown_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def page_json_list(raw_value: str | None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw_value or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def page_tags(raw_value: str | None) -> list[str]:
    try:
        value = json.loads(raw_value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def safe_json(raw_value: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw_value or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def track_event(
    db: Session,
    event_name: str,
    properties: dict[str, Any] | None = None,
    *,
    candidate_id: int | None = None,
    raw_source_id: int | None = None,
    page_id: str | None = None,
) -> None:
    TrackingService(db).track(
        event_name,
        properties,
        candidate_id=candidate_id,
        raw_source_id=raw_source_id,
        page_id=page_id,
    )


def pages_for_raw_source(db: Session, raw_source_id: int | None) -> list[WikiPage]:
    if raw_source_id is None:
        return []
    pages: list[WikiPage] = []
    for page in db.query(WikiPage).order_by(WikiPage.last_updated_at.desc()).all():
        refs = page_json_list(page.source_refs_json)
        if any(int(ref.get("raw_source_id") or 0) == raw_source_id for ref in refs):
            pages.append(page)
    return pages


def existing_context_for_ledger(db: Session, ledger: SyncLedgerItem) -> dict[str, Any]:
    candidate = db.get(CandidateItem, ledger.candidate_id) if ledger.candidate_id else None
    raw_source = db.query(RawSource).filter(RawSource.candidate_id == candidate.id).first() if candidate else None
    pages = pages_for_raw_source(db, raw_source.id if raw_source else None)
    latest_page = pages[0] if pages else None
    return {
        "candidate": candidate,
        "raw_source": raw_source,
        "latest_page": latest_page,
        "candidate_url": f"/ui/task/candidate/{candidate.id}" if candidate else "",
        "source_url": f"/ui/sources?source_id={raw_source.id}" if raw_source else "",
        "page_url": f"/ui/review/{latest_page.page_id}" if latest_page and latest_page.status == "needs_review" else (
            f"/ui/wiki?page_id={latest_page.page_id}" if latest_page else ""
        ),
    }


def duplicate_query_params(existing_context: dict[str, Any]) -> str:
    candidate = existing_context.get("candidate")
    raw_source = existing_context.get("raw_source")
    latest_page = existing_context.get("latest_page")
    params = {
        "duplicate": "link",
        "existing_candidate_id": candidate.id if candidate else "",
        "existing_source_id": raw_source.id if raw_source else "",
        "existing_page_id": latest_page.page_id if latest_page else "",
        "existing_url": existing_context.get("candidate").canonical_url if candidate else "",
    }
    return urlencode({key: value for key, value in params.items() if value})


def task_view_model(db: Session, candidate: CandidateItem, raw_source: RawSource | None, pages: list[WikiPage]) -> dict[str, Any]:
    latest_page = pages[0] if pages else None
    is_reviewed = bool(latest_page and latest_page.status == "active")
    if not raw_source:
        current_step = "save-source"
        primary_action = "保存来源证据"
        summary = "先把输入保存成可追溯的来源证据。"
    elif not latest_page:
        current_step = "generate-page"
        primary_action = "生成可审核结果"
        summary = "来源证据已保留，下一步生成带来源的蒸馏草稿。"
    elif latest_page.status == "needs_review":
        current_step = "review-result"
        primary_action = "审核 AI 草稿"
        summary = "AI 已生成草稿，必须由你确认后才进入知识库。"
    else:
        current_step = "ask-page"
        primary_action = "基于页面提问"
        summary = "页面已保存，下一步通过提问完成首次复用。"
    return {
        "current_step": current_step,
        "primary_action": primary_action,
        "summary": summary,
        "latest_page": latest_page,
        "has_raw_source": raw_source is not None,
        "has_page": latest_page is not None,
        "is_reviewed": is_reviewed,
    }


def task_cards_for_candidates(db: Session, candidates: list[CandidateItem], classifications: dict[int, KnowledgeClassification]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for candidate in candidates:
        raw_source = db.query(RawSource).filter(RawSource.candidate_id == candidate.id).first()
        pages = pages_for_raw_source(db, raw_source.id if raw_source else None)
        view = task_view_model(db, candidate, raw_source, pages)
        cards.append(
            {
                "candidate": candidate,
                "classification": classifications.get(candidate.id),
                "raw_source": raw_source,
                "latest_page": view["latest_page"],
                "current_step": view["current_step"],
                "primary_action": view["primary_action"],
                "summary": view["summary"],
            }
        )
    return cards


def read_local_text(path_value: str | None) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def source_type_label(value: str | None) -> str:
    return {
        "active_connector": "收藏夹",
        "passive_link": "用户贴的链接",
        "manual_idea": "临时 idea",
        "distill_profile": "博主蒸馏",
    }.get(value or "", value or "收藏夹")


def get_model_profile(profile_id: str | None) -> dict[str, Any] | None:
    if not profile_id:
        return None
    return next((profile for profile in get_model_profiles().get("profiles", []) if profile.get("id") == profile_id), None)


def ensure_connector(db: Session, platform: str, name: str, connector_type: str) -> Connector:
    connector = db.query(Connector).filter(Connector.platform == platform, Connector.connector_type == connector_type).first()
    if connector:
        return connector
    connector = Connector(name=name, platform=platform, connector_type=connector_type, status="active", auth_method="browser")
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector


def html_redirect_target(data: dict[str, Any], fallback: str) -> str:
    target = str(data.get("next") or fallback)
    if not target.startswith("/"):
        return fallback
    return target


def parse_collection_limit(data: dict[str, Any], default: int | None = 10) -> int | None:
    raw_mode = str(data.get("collection_limit") or data.get("limit_mode") or data.get("limit") or default or "10").strip()
    if raw_mode == "all":
        return None
    if raw_mode == "custom":
        raw_mode = str(data.get("custom_limit") or "10").strip()
    try:
        limit = int(raw_mode)
    except ValueError:
        limit = default or 10
    return max(1, min(int(limit or 10), 1000))


def truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "on", "yes", "y"}


def _pacing_params(data: dict[str, Any]) -> tuple[int, float, float]:
    """读取批量提取的节奏节流参数：每 N 条换窗、条间随机延时 [min, max] 秒。

    优先用请求体覆盖（便于实测调小），否则取 config 默认。
    """
    try:
        switch_every = int(data.get("switch_every") or DOUBAO_SWITCH_CONVO_EVERY)
    except (TypeError, ValueError):
        switch_every = DOUBAO_SWITCH_CONVO_EVERY
    try:
        delay_min = float(data.get("item_delay_min") if data.get("item_delay_min") is not None else DOUBAO_ITEM_DELAY_MIN)
    except (TypeError, ValueError):
        delay_min = DOUBAO_ITEM_DELAY_MIN
    try:
        delay_max = float(data.get("item_delay_max") if data.get("item_delay_max") is not None else DOUBAO_ITEM_DELAY_MAX)
    except (TypeError, ValueError):
        delay_max = DOUBAO_ITEM_DELAY_MAX
    switch_every = max(1, switch_every)
    delay_min = max(0.0, delay_min)
    delay_max = max(delay_min, delay_max)
    return switch_every, delay_min, delay_max


def _filter_pending_candidates(db: Session, candidate_ids: list[Any], platform: str) -> list[int]:
    """从 candidate_ids 里筛出该平台尚未提取的（断点续跑用，幂等跳过已完成）。"""
    from app.connectors.extract_pacing import next_pending

    metas: list[tuple[int, dict[str, Any]]] = []
    for raw_id in candidate_ids:
        try:
            cid = int(raw_id)
        except (TypeError, ValueError):
            continue
        candidate = db.get(CandidateItem, cid)
        meta = safe_json(candidate.metadata_json) if candidate is not None else {}
        metas.append((cid, meta))
    return next_pending(metas, platform)


async def process_candidate_ids(db: Session, candidate_ids: list[int], limit: int = 10) -> list[dict[str, Any]]:
    processed: list[dict[str, Any]] = []
    raw_service = RawSourceService(db)
    wiki_service = WikiMaintenanceService(db)
    for candidate_id in candidate_ids[:limit]:
        raw_source = raw_service.ingest_candidate(candidate_id)
        page = await wiki_service.create_page_from_raw_source(raw_source.id)
        processed.append({"candidate_id": candidate_id, "raw_source_id": raw_source.id, "wiki_page_id": page.page_id})
    return processed


async def classify_and_route_candidates(db: Session, candidate_ids: list[int], limit: int = 20) -> list[dict[str, Any]]:
    classifier = ClassifierService(db)
    raw_service = RawSourceService(db)
    routed: list[dict[str, Any]] = []
    for candidate_id in candidate_ids[:limit]:
        result = await classifier.classify_candidate(candidate_id)
        raw_source_id = None
        if result.status == CLASSIFIED_KNOWLEDGE:
            raw_source = raw_service.ingest_candidate(candidate_id)
            raw_source_id = raw_source.id
        routed.append(
            {
                "candidate_id": candidate_id,
                "label": result.label,
                "confidence": result.confidence,
                "status": result.status,
                "raw_source_id": raw_source_id,
            }
        )
    return routed


def build_douyin_items(raw_items: Any, limit: int | None = 10, source: str = "douyin_computer_use_favorites") -> list[ConnectorItem]:
    if isinstance(raw_items, str):
        try:
            raw_items = json.loads(raw_items)
        except json.JSONDecodeError:
            raw_items = []
    if not isinstance(raw_items, list):
        return []
    connector_items: list[ConnectorItem] = []
    for raw_item in raw_items[: limit or 1000]:
        if not isinstance(raw_item, dict):
            continue
        href = str(raw_item.get("href") or raw_item.get("url") or raw_item.get("raw_url") or "").strip()
        if not href:
            continue
        page_text = str(raw_item.get("pageText") or raw_item.get("page_text") or raw_item.get("description") or "").strip()
        transcript = str(raw_item.get("transcript") or "").strip()
        title = clean_douyin_title(str(raw_item.get("title") or raw_item.get("desc") or "").strip(), page_text, href)
        content_type = str(raw_item.get("kind") or raw_item.get("content_type") or "video").strip()
        connector_items.append(
            ConnectorItem(
                raw_url=href,
                title=title,
                author=str(raw_item.get("author") or "").strip() or None,
                platform="douyin",
                content_type=content_type,
                metadata={
                    "source": source,
                    "page_text": page_text,
                    "transcript": transcript,
                    "douyin_page_url": raw_item.get("pageUrl") or raw_item.get("douyin_page_url"),
                    "extractor": source,
                },
            )
        )
    return connector_items


def enrich_douyin_items_with_report(
    items: list[ConnectorItem],
    service: DouyinTranscriptService,
    *,
    limit: int | None = None,
    require_transcript: bool = True,
) -> tuple[list[ConnectorItem], list[dict[str, str]]]:
    if not hasattr(service, "enrich_item"):
        return service.enrich_items(items, limit=limit, require_transcript=require_transcript), []
    enriched: list[ConnectorItem] = []
    failures: list[dict[str, str]] = []
    for item in items[: limit or len(items)]:
        try:
            enriched_item = service.enrich_item(item, require_transcript=require_transcript)
        except DouyinTranscriptError as exc:
            failures.append({"url": item.raw_url, "title": item.title, "error": str(exc)})
            continue
        if require_transcript and not str((enriched_item.metadata or {}).get("transcript") or "").strip():
            failures.append({"url": item.raw_url, "title": item.title, "error": "ASR returned an empty transcript"})
            continue
        enriched.append(enriched_item)
    return enriched, failures


def douyin_profile_base_url(profile_url: str) -> str:
    parsed = urlparse(profile_url)
    if "douyin.com" not in parsed.netloc.lower() or not parsed.path.startswith("/user/"):
        return profile_url
    return urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def douyin_profile_vid_fallback(profile_url: str, target_name: str) -> ConnectorItem | None:
    parsed = urlparse(profile_url)
    if "douyin.com" not in parsed.netloc.lower():
        return None
    vid = (parse_qs(parsed.query).get("vid") or parse_qs(parsed.query).get("modal_id") or [""])[0].strip()
    if not vid:
        return None
    return ConnectorItem(
        raw_url=f"https://www.douyin.com/video/{vid}",
        title=f"{target_name} 主页视频 {vid}",
        author=target_name,
        platform="douyin",
        content_type="video",
        metadata={"source": "douyin_creator_profile_vid_fallback", "profile_url": profile_url},
    )


def clean_douyin_title(raw_title: str, page_text: str, href: str) -> str:
    title = raw_title.strip()
    if title and not title.startswith("http") and len(title) >= 4:
        return title[:180]
    bad_line = {"首页", "推荐", "关注", "朋友", "我的", "搜索", "收藏夹", "视频", "音乐", "合集", "短剧"}
    candidates = []
    for line in str(page_text or "").replace("\u200b", "").splitlines():
        line = line.strip()
        if not line or line in bad_line:
            continue
        if line.replace(".", "", 1).isdigit() or line.endswith("万") or line.endswith("亿"):
            continue
        if len(line) < 4:
            continue
        candidates.append(line)
    if candidates:
        candidates.sort(key=lambda value: len(value), reverse=True)
        return candidates[0][:180]
    return href


def filter_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        **metadata,
        "filter_usefulness": str(item.get("usefulness") or item.get("filter_usefulness") or "useful"),
        "filter_subcategory": str(item.get("subcategory") or item.get("domain") or item.get("filter_subcategory") or "未分类"),
        "filter_reason": str(item.get("reason") or item.get("filter_reason") or "用户在历史收藏预筛选中保留。"),
        "filter_confidence": float(item.get("confidence") or item.get("filter_confidence") or 0),
        "source": metadata.get("source") or "historical_favorites_filter",
    }


def connector_item_from_filter_item(item: dict[str, Any], platform: str) -> ConnectorItem:
    return ConnectorItem(
        raw_url=str(item.get("url") or item.get("raw_url") or "").strip(),
        title=str(item.get("title") or "").strip(),
        platform=platform,
        author=str(item.get("author") or "").strip() or None,
        content_type=str(item.get("content_type") or "auto").strip() or "auto",
        metadata=filter_metadata(item),
    )


def candidate_ids_for_items(db: Session, items: list[ConnectorItem]) -> list[int]:
    candidate_ids: list[int] = []
    seen: set[int] = set()
    for item in items:
        normalized = normalize_url(item.raw_url, item.platform)
        candidate = (
            db.query(CandidateItem)
            .filter(
                or_(
                    CandidateItem.canonical_url == normalized.canonical_url,
                    CandidateItem.raw_url == item.raw_url,
                    (CandidateItem.platform == normalized.platform) & (CandidateItem.external_item_id == normalized.external_item_id),
                )
            )
            .first()
        )
        if candidate and candidate.id not in seen:
            metadata = json.loads(candidate.metadata_json or "{}")
            raw_source = db.query(RawSource).filter(RawSource.candidate_id == candidate.id).first()
            already_extracted = metadata.get("doubao_extracted") is True or metadata.get("xiaohongshu_diandian_extracted") is True
            if already_extracted or raw_source is not None:
                continue
            metadata.update(item.metadata or {})
            candidate.title = item.title or candidate.title
            candidate.author = item.author or candidate.author
            candidate.content_type = item.content_type or candidate.content_type
            candidate.metadata_json = json.dumps(metadata, ensure_ascii=False)
            candidate_ids.append(candidate.id)
            seen.add(candidate.id)
    if candidate_ids:
        db.commit()
    return candidate_ids


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "starmind-local"}


@router.get("/ui/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "onboarding.html", template_context(request, "home", db))


@router.get("/ui/graph", response_class=HTMLResponse)
def graph_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "graph.html", template_context(request, "home", db))


@router.get("/api/graph")
def graph_api(request: Request, db: Session = Depends(get_db)):
    from app.services.graph_service import GraphService
    domain_filter = request.query_params.get("domain")
    return GraphService(db).get_graph_data(domain_filter=domain_filter)


@router.get("/api/graph/node/{page_id}")
def graph_node_detail(page_id: str, db: Session = Depends(get_db)):
    from app.services.graph_service import GraphService
    return GraphService(db).get_node_detail(page_id)


@router.post("/api/graph/rebuild")
async def graph_rebuild(db: Session = Depends(get_db)):
    from app.services.wiki_service import WikiMaintenanceService

    svc = WikiMaintenanceService(db)
    pages = db.query(WikiPage).filter(WikiPage.status.in_(["active", "needs_review"])).all()
    edges_created = await svc.rebuild_ai_edges_for_pages(pages)
    return {"status": "ok", "edges_created": edges_created, "total_pages": len(pages)}


@router.post("/ui/language")
async def save_ui_language(request: Request):
    data = await request_data(request)
    language = str(data.get("language") or "zh").strip()
    if language not in {"zh", "en"}:
        language = "zh"
    write_json(UI_PREFS_PATH, {"language": language})
    if wants_html(request):
        return RedirectResponse(html_redirect_target(data, "/"), status_code=303)
    return {"status": "saved", "language": language}


@router.post("/events/v3")
async def v3_ui_event(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    event_name = str(data.get("event_name") or data.get("event") or "").strip()
    if event_name not in V3_UI_EVENT_NAMES:
        raise HTTPException(status_code=400, detail="unsupported V3 event")
    track_event(
        db,
        event_name,
        {
            "entry_mode": data.get("entry_mode") or "",
            "entry": data.get("entry") or "",
            "input_type": data.get("input_type") or "",
            "length_bucket": data.get("length_bucket") or "",
            "demo_id": data.get("demo_id") or "",
            "viewport": data.get("viewport") or "",
        },
    )
    return JSONResponse({"status": "tracked", "event_name": event_name})


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    # Check onboarding — only redirect if user has never interacted
    from app.models import OnboardingStatus
    onboarding = db.query(OnboardingStatus).first()
    if onboarding and not onboarding.completed_at and not onboarding.skipped and onboarding.current_step == 0:
        return RedirectResponse("/ui/onboarding", status_code=303)

    track_event(db, "page_viewed", {"page": "home"})
    has_history = db.query(CandidateItem).count() > 0 or db.query(WikiPage).count() > 0
    track_event(
        db,
        "v3_home_viewed",
        {
            "visitor_state": "returning" if has_history else "new",
            "has_history": has_history,
            "pending_count": db.query(CandidateItem).filter(CandidateItem.status.in_(REVIEWABLE_STATUSES)).count(),
        },
    )
    home_preview = get_v3_home_preview()
    track_event(db, "v3_demo_preview_viewed", {"demo_id": home_preview["demo_id"]})
    connectors = db.query(Connector).order_by(Connector.created_at.desc()).all()
    settings = get_model_settings()
    profiles = get_model_profiles()
    recent_candidates = db.query(CandidateItem).order_by(CandidateItem.created_at.desc()).limit(6).all()
    classifications = latest_classifications(db, [candidate.id for candidate in recent_candidates])
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        template_context(
            request,
            "home",
            db,
            recent_logs=db.query(ScanLog).order_by(ScanLog.created_at.desc()).limit(8).all(),
            distill_requests=get_distill_requests()["requests"][:3],
            agent_legion=get_agent_legion()["agents"],
            created=request.query_params.get("created"),
            scan=request.query_params.get("scan"),
            connectors=connectors,
            settings=settings,
            model_profiles=profiles["profiles"],
            active_profile_id=get_active_profile_id(settings, profiles),
            pages=db.query(WikiPage).order_by(WikiPage.last_updated_at.desc()).limit(3).all(),
            sources=db.query(RawSource).order_by(RawSource.created_at.desc()).limit(3).all(),
            task_cards=task_cards_for_candidates(db, recent_candidates, classifications),
            demo=get_demo_result(),
            demo_results=list_demo_results(),
            home_preview=home_preview,
            v3_entries=list(V3_ENTRY_MODES.values()),
            input_error=request.query_params.get("input_error"),
            entry_mode=request.query_params.get("entry_mode") or "link",
            has_history=has_history,
        ),
    )


@router.post("/ui/v3/input")
async def v3_home_input(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    route = classify_v3_input(str(data.get("content") or ""), str(data.get("entry_mode") or "link"))
    if route.is_empty:
        track_event(
            db,
            "v3_task_create_failed",
            {"reason": "empty_input", "entry_mode": route.entry_mode, "input_type": route.input_type},
        )
        return RedirectResponse(f"/?input_error=empty&entry_mode={route.entry_mode}", status_code=303)
    track_event(
        db,
        "v3_primary_input_submitted",
        {
            "entry_mode": route.entry_mode,
            "mode": route.mode,
            "input_type": route.input_type,
            "length_bucket": route.length_bucket,
            "from_home": True,
        },
    )
    params = {
        "mode": route.mode,
        "entry_mode": route.entry_mode,
        "input_type": route.input_type,
        "source": "home_input",
    }
    if route.mode == "favorites":
        return RedirectResponse("/ui/sync", status_code=303)
    if route.content:
        params["prefill"] = route.content[:1600]
    return RedirectResponse(f"/ui/create?{urlencode(params)}", status_code=303)


@router.get("/ui/create", response_class=HTMLResponse)
def create_task_page(request: Request, db: Session = Depends(get_db)):
    source = request.query_params.get("source")
    if source == "home_cta":
        track_event(db, "homepage_cta_clicked", {"cta": "create_task", "source": "home"})
    track_event(db, "page_viewed", {"page": "task_create", "source": source or ""})
    mode = request.query_params.get("mode") or "link"
    prefill = request.query_params.get("prefill") or ""
    v3_input = classify_v3_input(prefill, mode)
    return templates.TemplateResponse(
        request,
        "task_create.html",
        template_context(
            request,
            "create",
            db,
            mode=v3_input.mode,
            entry_mode=request.query_params.get("entry_mode") or v3_input.entry_mode,
            input_type=request.query_params.get("input_type") or v3_input.input_type,
            prefill=prefill,
            v3_input=v3_input,
            v3_entries=list(V3_ENTRY_MODES.values()),
            created=request.query_params.get("created"),
            duplicate=request.query_params.get("duplicate"),
            existing_candidate_id=request.query_params.get("existing_candidate_id"),
            existing_source_id=request.query_params.get("existing_source_id"),
            existing_page_id=request.query_params.get("existing_page_id"),
            existing_url=request.query_params.get("existing_url"),
            demo_results=list_demo_results(),
        ),
    )


@router.get("/ui/demo", response_class=HTMLResponse)
def demo_result_page(request: Request, db: Session = Depends(get_db)):
    demo_id = request.query_params.get("demo_id") or "second-brain"
    demo = get_demo_result(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo result not found")
    track_event(db, "demo_viewed", {"demo_id": demo_id, "demo_type": demo.get("demo_type", "")})
    demo_wiki = demo.get("wiki", {})
    demo_markdown = "\n\n".join(
        [
            f"# {demo_wiki.get('title') or demo.get('title')}",
            str(demo_wiki.get("summary") or ""),
            "## 关键要点",
            "\n".join(f"- {item}" for item in demo_wiki.get("bullets", [])),
        ]
    )
    return templates.TemplateResponse(
        request,
        "demo_result.html",
        template_context(
            request,
            "home",
            db,
            demo=demo,
            demo_results=list_demo_results(),
            markdown_html=render_markdown(demo_markdown),
        ),
    )


@router.get("/ui/task/candidate/{candidate_id}", response_class=HTMLResponse)
def candidate_task_detail(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    candidate = db.get(CandidateItem, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    track_event(db, "page_viewed", {"page": "task_detail"}, candidate_id=candidate.id)
    raw_source = db.query(RawSource).filter(RawSource.candidate_id == candidate.id).first()
    pages = pages_for_raw_source(db, raw_source.id if raw_source else None)
    latest = latest_classifications(db, [candidate.id]).get(candidate.id)
    return templates.TemplateResponse(
        request,
        "task_detail.html",
        template_context(
            request,
            "history",
            db,
            candidate=candidate,
            classification=latest,
            raw_source=raw_source,
            pages=pages,
            task=task_view_model(db, candidate, raw_source, pages),
            created=request.query_params.get("created"),
        ),
    )


@router.get("/ui/review/{page_id}", response_class=HTMLResponse)
def review_page(page_id: str, request: Request, db: Session = Depends(get_db)):
    page = db.query(WikiPage).filter(WikiPage.page_id == page_id).first()
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    track_event(db, "page_viewed", {"page": "result_review"}, page_id=page.page_id)
    track_event(db, "result_viewed", {"status": page.status, "page_type": page.page_type}, page_id=page.page_id)
    refs = page_json_list(page.source_refs_json)
    source_map = {source.id: source for source in db.query(RawSource).all()}
    markdown = read_wiki_markdown(page)
    quality = compute_page_quality(page, markdown, source_map)
    track_event(
        db,
        "v3_result_viewed",
        {
            "quality_level": quality.quality_level,
            "source_count": quality.source_refs_count,
            "page_type": page.page_type,
        },
        page_id=page.page_id,
    )
    return templates.TemplateResponse(
        request,
        "result_review.html",
        template_context(
            request,
            "wiki",
            db,
            page=page,
            markdown=markdown,
            markdown_html=render_markdown(markdown),
            summary=markdown_summary(markdown),
            key_points=markdown_key_points(markdown),
            quality=quality,
            quality_label=quality_label(quality.quality_level),
            generation_status_label=generation_label(quality.generation_status),
            transcript_status_label=transcript_label(quality.transcript_status),
            suggested_questions=suggested_questions(page.title),
            refs=refs,
            source_map=source_map,
            validation_error=request.query_params.get("error"),
            saved=request.query_params.get("saved"),
        ),
    )


@router.post("/wiki/pages/{page_id}/confirm")
async def confirm_review_page(page_id: str, request: Request, db: Session = Depends(get_db)):
    page = db.query(WikiPage).filter(WikiPage.page_id == page_id).first()
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    data = await request_data(request)
    markdown = str(data.get("markdown") or "").strip()
    refs = page_json_list(page.source_refs_json)
    if not refs:
        if wants_html(request):
            return RedirectResponse(f"/ui/review/{page_id}?error=missing-source", status_code=303)
        raise HTTPException(status_code=400, detail="source_refs required")
    if not markdown:
        if wants_html(request):
            return RedirectResponse(f"/ui/review/{page_id}?error=empty-body", status_code=303)
        raise HTTPException(status_code=400, detail="markdown required")
    path = Path(page.markdown_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    page.status = "active"
    page.updated_by = "user_reviewed"
    db.commit()
    track_event(db, "result_confirmed", {"page_type": page.page_type}, page_id=page.page_id)
    track_event(db, "v3_result_confirmed", {"page_type": page.page_type}, page_id=page.page_id)
    track_event(db, "v3_result_saved", {"page_type": page.page_type}, page_id=page.page_id)
    if wants_html(request):
        return RedirectResponse(f"/ui/review/{page.page_id}?saved=review-confirmed", status_code=303)
    return {"status": "confirmed", "page_id": page.page_id}


@router.get("/ui/guide", response_class=HTMLResponse)
def guide_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "guide.html", template_context(request, "guide", db))


@router.get("/ui/sync", response_class=HTMLResponse)
def sync_favorites_page(request: Request, db: Session = Depends(get_db)):
    cards = favorite_platform_cards(db)
    live_platforms = [card for card in cards if card["support_level"] == "live"]
    planned_platforms = [card for card in cards if card["support_level"] != "live"]
    return templates.TemplateResponse(
        request,
        "sync_favorites.html",
        template_context(
            request,
            "sync",
            db,
            favorite_platforms=cards,
            live_platforms=live_platforms,
            planned_platforms=planned_platforms,
        ),
    )


@router.get("/ui/import-link", response_class=HTMLResponse)
def import_link_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "import_link.html",
        template_context(request, "home", db, created=request.query_params.get("created")),
    )


@router.get("/ui/idea", response_class=HTMLResponse)
def idea_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "idea_capture.html",
        template_context(request, "home", db, created=request.query_params.get("created")),
    )


@router.get("/ui/distill", response_class=HTMLResponse)
def distill_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "distill_profile.html",
        template_context(
            request,
            "home",
            db,
            distill_requests=get_distill_requests()["requests"],
            created=request.query_params.get("created"),
        ),
    )


@router.get("/ui/workbench", response_class=HTMLResponse)
def workbench_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse("/ui/sources", status_code=303)


@router.get("/workbench/modules")
def workbench_modules() -> dict[str, Any]:
    return {"modules": WORKBENCH_MODULES}


@router.get("/workbench/layout")
def workbench_layout() -> dict[str, Any]:
    return get_workbench_layout()


@router.post("/workbench/layout")
async def save_workbench_layout(request: Request) -> dict[str, Any]:
    data = await request.json()
    modules = data.get("modules", [])
    normalized = []
    for index, module in enumerate(modules):
        module_id = str(module.get("id", "")).strip()
        if not module_id:
            continue
        normalized.append(
            {
                "id": module_id,
                "position": int(module.get("position", index)),
                "size": str(module.get("size", "medium")),
                "settings": module.get("settings", {}),
            }
        )
    payload = {"modules": sorted(normalized, key=lambda item: item["position"])}
    write_json(WORKBENCH_LAYOUT_PATH, payload)
    return {"ok": True, "layout": payload}


@router.get("/api/conversations")
def list_conversations(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    conversations = (
        db.query(ChatConversation)
        .join(ChatMessage)
        .group_by(ChatConversation.id)
        .order_by(ChatConversation.updated_at.desc())
        .all()
    )
    return [conversation_payload(conversation) for conversation in conversations]


@router.post("/api/conversations")
def create_conversation(db: Session = Depends(get_db)) -> dict[str, Any]:
    conversation = ChatConversation(id=f"chat-{uuid4().hex}", title="新对话")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation_payload(conversation)


@router.get("/api/conversations/{conversation_id}/messages")
def list_conversation_messages(conversation_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .all()
    )
    return [message_payload(message) for message in messages]


@router.post("/api/conversations/{conversation_id}/messages")
async def create_conversation_message(conversation_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    data = await request.json()
    question = str(data.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    db.add(ChatMessage(conversation_id=conversation_id, role="user", content=question))
    if conversation.title == "新对话":
        conversation.title = question[:50]
    answer = await AgentRunner(db).answer_question(question)
    assistant_message = ChatMessage(
        conversation_id=conversation_id,
        role="assistant",
        content=answer.answer,
        sources_json=json.dumps(answer.sources, ensure_ascii=False),
    )
    conversation.updated_at = datetime.utcnow()
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)
    return message_payload(assistant_message)


@router.post("/api/conversations/{conversation_id}/rename")
async def rename_conversation(conversation_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    data = await request.json()
    title = str(data.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    conversation.title = title[:200]
    db.commit()
    db.refresh(conversation)
    return conversation_payload(conversation)


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    db.delete(conversation)
    db.commit()
    return {"ok": True}


def settings_page_context(request: Request, db: Session) -> dict[str, Any]:
    settings = get_model_settings()
    profiles = get_model_profiles()
    return template_context(
        request,
        "settings",
        db,
        settings=settings,
        providers=get_providers(),
        model_profiles=profiles["profiles"],
        active_profile_id=get_active_profile_id(settings, profiles),
        status=request.query_params.get("status"),
        test=request.query_params.get("test"),
        test_error=request.query_params.get("error"),
        test_provider=request.query_params.get("provider"),
    )


@router.get("/ui/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "settings.html", settings_page_context(request, db))


@router.get("/ui/settings/model", response_class=HTMLResponse)
def model_settings_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "model_settings.html", settings_page_context(request, db))


@router.get("/settings/providers")
def providers_api() -> dict[str, Any]:
    return get_providers()


@router.post("/settings/providers/custom")
async def custom_provider(request: Request):
    data = await request_data(request)
    result = add_custom_provider(
        display_name=str(data.get("display_name", "Custom Provider")),
        base_url=str(data.get("base_url", "")),
        model=str(data.get("model", "custom-model")),
        api_key_label=str(data.get("api_key_label", "API Key")),
    )
    if wants_html(request):
        return RedirectResponse("/ui/settings?status=custom-provider-added", status_code=303)
    return result


@router.get("/settings/model")
def model_settings_api() -> dict[str, Any]:
    settings = get_model_settings()
    settings["providers"] = get_providers()
    return settings


@router.post("/settings/model")
async def update_model_settings(request: Request):
    data = await request_data(request)
    provider = str(data.get("provider") or data.get("default_provider") or "mock")
    model = str(data.get("model") or data.get("default_model") or "mock-fast")
    base_url = str(data.get("base_url") or "").strip() if "base_url" in data else None
    api_key = str(data.get("api_key") or "").strip() or None
    result = save_model_settings(provider=provider, model=model, api_key=api_key, base_url=base_url)
    if wants_html(request):
        return RedirectResponse("/ui/settings?status=saved", status_code=303)
    return result


@router.post("/settings/model/test")
async def test_model_settings(request: Request):
    data = await request_data(request)
    provider = str(data.get("provider") or data.get("default_provider") or "").strip()
    model = str(data.get("model") or data.get("default_model") or "").strip()
    base_url = str(data.get("base_url") or "").strip()
    api_key = str(data.get("api_key") or "").strip() or None
    if provider and base_url:
        save_model_settings(provider=provider, model=model, base_url=base_url)
    result = await test_model_connection(provider, model, api_key, base_url) if provider else await test_active_connection()
    if wants_html(request):
        state = "success" if result["ok"] else "failed"
        params = {"test": state, "provider": result.get("provider") or provider}
        if not result["ok"] and result.get("error"):
            params["error"] = str(result.get("error"))
        return RedirectResponse(f"/ui/settings?{urlencode(params)}", status_code=303)
    return result


@router.post("/settings/model/clear-key")
async def clear_model_key(request: Request):
    data = await request_data(request)
    provider = str(data.get("provider") or get_model_settings().get("default_provider") or "mock")
    result = clear_api_key(provider)
    if wants_html(request):
        return RedirectResponse("/ui/settings?status=key-cleared", status_code=303)
    return result


@router.get("/settings/model-profiles")
def model_profiles_api() -> dict[str, Any]:
    return get_model_profiles()


@router.post("/settings/model-profiles")
async def create_model_profile(request: Request):
    data = await request_data(request)
    provider = str(data.get("provider") or "mock").strip()
    model = str(data.get("model") or "mock-fast").strip()
    base_url = str(data.get("base_url") or "").strip()
    provider_meta = get_providers().get(provider, {})
    default_name = f"{provider_meta.get('display_name', provider)} · {model}"
    name = str(data.get("name") or default_name).strip() or default_name
    use_case = str(data.get("use_case") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    activate = str(data.get("activate") or "on").lower() in {"1", "true", "on", "yes"}
    if api_key:
        save_provider_api_key(provider, api_key)
    if base_url:
        save_provider_base_url(provider, base_url)
    payload = get_model_profiles()
    profile = next((item for item in payload["profiles"] if item.get("name") == name), None)
    if profile is None:
        profile = {"id": f"profile-{uuid4().hex}"}
        payload["profiles"].insert(0, profile)
    profile.update({"name": name, "provider": provider, "model": model, "use_case": use_case})
    if activate:
        payload["active_profile_id"] = profile["id"]
    write_json(MODEL_PROFILES_PATH, payload)
    if activate:
        save_model_settings(provider=provider, model=model, api_key=api_key or None, base_url=base_url)
    if wants_html(request):
        status = "model-profile-activated" if activate else "model-profile-saved"
        return RedirectResponse(f"/ui/settings?status={status}", status_code=303)
    return {"status": "created", "profile": profile}


@router.post("/settings/model-profiles/{profile_id}/activate")
async def activate_model_profile(profile_id: str, request: Request):
    payload = get_model_profiles()
    profiles = payload["profiles"]
    profile = next((item for item in profiles if item["id"] == profile_id), None)
    if profile is None:
        raise HTTPException(status_code=404, detail="model profile not found")
    payload["active_profile_id"] = profile_id
    write_json(MODEL_PROFILES_PATH, payload)
    result = save_model_settings(provider=profile["provider"], model=profile["model"])
    if wants_html(request):
        return RedirectResponse("/ui/settings?status=model-profile-activated", status_code=303)
    return {"status": "activated", "profile": profile, "settings": result}


@router.get("/ui/connectors", response_class=HTMLResponse)
def connectors_page(request: Request, db: Session = Depends(get_db)):
    connectors = db.query(Connector).order_by(Connector.created_at.desc()).all()
    source_connections = get_source_connections()["connections"]
    return templates.TemplateResponse(
        request,
        "connectors.html",
        template_context(
            request,
            "connectors",
            db,
            connectors=connectors,
            connectors_by_platform={connector.platform: connector for connector in connectors},
            recent_logs=db.query(ScanLog).order_by(ScanLog.created_at.desc()).limit(12).all(),
            recent_ledger=db.query(SyncLedgerItem).order_by(SyncLedgerItem.created_at.desc()).limit(8).all(),
            distill_requests=get_distill_requests()["requests"],
            scan=request.query_params.get("scan"),
            created=request.query_params.get("created"),
            platform_presets=sorted(PLATFORM_PRESETS, key=lambda item: int(item["priority"])),
            platform_exclusions=PLATFORM_EXCLUSIONS,
            source_connections=source_connections,
        ),
    )


def _source_setup_context(platform: str, db: Session, saved: str | None = None) -> dict[str, Any] | None:
    """准备 source_setup 模板/片段的 context。未知平台返回 None。

    独立页 source_setup_page 与片段端点 source_setup_panel 共用，保证两处行为一致。
    """
    preset = next((item for item in PLATFORM_PRESETS if item["platform"] == platform), None)
    if preset is None:
        return None
    source_connections = get_source_connections()["connections"]
    connection = source_connections.get(platform, {})
    saved_homepage_url = str(connection.get("homepage_url") or "").strip() if isinstance(connection, dict) else ""
    has_saved_favorites_url = platform in {"bilibili", "xiaohongshu"} and bool(saved_homepage_url)
    open_label = "打开已保存收藏页" if has_saved_favorites_url else PLATFORM_OPEN_LABELS.get(platform, f"打开 {preset['name']} 官网登录")
    connector = db.query(Connector).filter(Connector.platform == platform).first()
    return {
        "preset": preset,
        "connector": connector,
        "connection": connection,
        "has_saved_favorites_url": has_saved_favorites_url,
        "open_label": open_label,
        "saved": saved,
    }


@router.get("/ui/source-setup/{platform}", response_class=HTMLResponse)
def source_setup_page(platform: str, request: Request, db: Session = Depends(get_db)):
    ctx = _source_setup_context(platform, db, saved=request.query_params.get("saved"))
    if ctx is None:
        raise HTTPException(status_code=404, detail="source platform not found")
    return templates.TemplateResponse(
        request,
        "source_setup.html",
        template_context(request, "connectors", db, **ctx),
    )


@router.get("/ui/source-setup/{platform}/panel", response_class=HTMLResponse)
def source_setup_panel(platform: str, request: Request, db: Session = Depends(get_db)):
    """只渲染提取工作台片段（不套 base.html），供 /ui/sync 标签栏按需 AJAX 注入。"""
    ctx = _source_setup_context(platform, db, saved=request.query_params.get("saved"))
    if ctx is None:
        raise HTTPException(status_code=404, detail="source platform not found")
    return templates.TemplateResponse(request, "_source_setup_panel.html", ctx)



@router.post("/source-connections/{platform}/save-current-page")
async def save_current_source_page(platform: str, request: Request, db: Session = Depends(get_db)):
    preset = next((item for item in PLATFORM_PRESETS if item["platform"] == platform), None)
    if preset is None:
        raise HTTPException(status_code=404, detail="source platform not found")
    if platform not in {"bilibili", "xiaohongshu"}:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    try:
        await cdp_proxy.connect()
        targets = await cdp_proxy.list_targets()
    except CDPConnectionError as exc:
        if wants_html(request):
            return RedirectResponse(f"/ui/source-setup/{platform}?saved=browser-missing", status_code=303)
        raise HTTPException(status_code=503, detail={"code": "cdp_proxy_error", "message": str(exc)}) from exc
    candidates = []
    for target in targets:
        url = str(target.get("url") or "").strip()
        if is_platform_favorites_url(platform, url):
            candidates.append(url)
    unique_candidates = list(dict.fromkeys(candidates))
    if not unique_candidates:
        if wants_html(request):
            return RedirectResponse(f"/ui/source-setup/{platform}?saved=favorites-page-not-found", status_code=303)
        raise HTTPException(
            status_code=428,
            detail={
                "code": "favorites_page_not_found",
                "message": "没有在当前浏览器标签页中找到真实收藏页。请先在浏览器里打开该平台收藏页，再点击保存当前收藏页。",
            },
        )
    if len(unique_candidates) > 1:
        if wants_html(request):
            return RedirectResponse(f"/ui/source-setup/{platform}?saved=multiple-favorites-pages", status_code=303)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "multiple_favorites_pages_found",
                "message": "找到了多个收藏页，请只保留一个目标收藏页后重试。",
                "candidates": unique_candidates,
            },
        )

    payload = get_source_connections()
    connection = dict(payload.get("connections", {}).get(platform, {}))
    connection.update(
        {
            "platform": platform,
            "display_name": str(preset["name"]),
            "auth_method": str(connection.get("auth_method") or preset.get("auth_hint") or "manual"),
            "homepage_url": unique_candidates[0],
            "api_base_url": str(connection.get("api_base_url") or ""),
            "sync_scope": str(connection.get("sync_scope") or "favorites"),
            "notes": str(connection.get("notes") or ""),
            "status": str(connection.get("status") or "configured_only"),
            "supports_live_sync": bool(connection.get("supports_live_sync") or False),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    payload.setdefault("connections", {})[platform] = connection
    write_json(SOURCE_CONNECTIONS_PATH, payload)

    connector = db.query(Connector).filter(Connector.platform == platform).first()
    if connector is None:
        connector = Connector(
            name=str(preset["name"]),
            platform=platform,
            connector_type="platform_stub",
            status="configured_only",
            auth_method=connection["auth_method"],
            max_scan_pages=20,
        )
        db.add(connector)
    else:
        connector.auth_method = connection["auth_method"]
        connector.status = "configured_only"
    db.commit()

    if wants_html(request):
        return RedirectResponse(f"/ui/source-setup/{platform}?saved=current-page-saved", status_code=303)
    return {"status": "saved", "connection": connection}


@router.post("/source-connections/{platform}")
async def save_source_connection(platform: str, request: Request, db: Session = Depends(get_db)):
    preset = next((item for item in PLATFORM_PRESETS if item["platform"] == platform), None)
    if preset is None:
        raise HTTPException(status_code=404, detail="source platform not found")
    data = await request_data(request)
    payload = get_source_connections()
    supports_live_sync = platform == "douyin"
    connection = {
        "platform": platform,
        "display_name": str(preset["name"]),
        "auth_method": str(data.get("auth_method") or preset.get("auth_hint") or "manual"),
        "homepage_url": str(data.get("homepage_url") or "").strip(),
        "api_base_url": str(data.get("api_base_url") or "").strip(),
        "sync_scope": str(data.get("sync_scope") or "favorites").strip(),
        "notes": str(data.get("notes") or "").strip(),
        "status": "ready" if supports_live_sync else "configured_only",
        "supports_live_sync": supports_live_sync,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    payload["connections"][platform] = connection
    write_json(SOURCE_CONNECTIONS_PATH, payload)

    connector = db.query(Connector).filter(Connector.platform == platform).first()
    if connector is None:
        connector = Connector(
            name=str(preset["name"]),
            platform=platform,
            connector_type="platform_stub",
            status="ready" if supports_live_sync else "configured_only",
            auth_method=connection["auth_method"],
            max_scan_pages=20,
        )
        db.add(connector)
    else:
        connector.auth_method = connection["auth_method"]
        connector.status = "ready" if supports_live_sync else "configured_only"
    db.commit()

    if wants_html(request):
        return RedirectResponse(f"/ui/source-setup/{platform}?saved=1", status_code=303)
    return {"status": "saved", "connection": connection}


@router.post("/bilibili/browser/open")
async def open_bilibili_browser(request: Request):
    data = await request_data(request)
    url = str(data.get("homepage_url") or data.get("url") or "").strip()
    try:
        state = await open_platform_browser("bilibili", url)
    except CDPConnectionError as exc:
        if wants_html(request):
            return RedirectResponse("/ui/source-setup/bilibili?saved=browser-missing", status_code=303)
        raise HTTPException(status_code=503, detail={"code": "cdp_proxy_error", "message": str(exc)}) from exc
    if wants_html(request):
        return RedirectResponse("/ui/source-setup/bilibili?saved=browser-opened", status_code=303)
    return {"status": "opened", "current_url": state["current_url"]}


@router.post("/xiaohongshu/browser/open")
async def open_xiaohongshu_browser(request: Request):
    data = await request_data(request)
    url = str(data.get("homepage_url") or data.get("url") or "").strip()
    try:
        state = await open_platform_browser("xiaohongshu", url)
    except CDPConnectionError as exc:
        if wants_html(request):
            return RedirectResponse("/ui/source-setup/xiaohongshu?saved=browser-missing", status_code=303)
        raise HTTPException(status_code=503, detail={"code": "cdp_proxy_error", "message": str(exc)}) from exc
    if wants_html(request):
        return RedirectResponse("/ui/source-setup/xiaohongshu?saved=browser-opened", status_code=303)
    return {"status": "opened", "current_url": state["current_url"]}


@router.get("/connectors")
def list_connectors(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [connector_to_dict(connector) for connector in db.query(Connector).order_by(Connector.created_at.desc()).all()]


@router.post("/connectors")
async def create_connector(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    connector = Connector(
        name=str(data.get("name") or "平台来源"),
        platform=str(data.get("platform") or "manual"),
        connector_type=str(data.get("connector_type") or "platform_stub"),
        status="active",
        auth_method=str(data.get("auth_method") or "none"),
        max_scan_pages=int(data.get("max_scan_pages") or 20),
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    if wants_html(request):
        return RedirectResponse(html_redirect_target(data, f"/ui/connectors?created={connector.id}"), status_code=303)
    return connector_to_dict(connector)


@router.post("/connectors/{connector_id}/scan")
async def scan_connector(connector_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        result = await SyncService(db).scan_connector(connector_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if wants_html(request):
        data = await request_data(request)
        return RedirectResponse(html_redirect_target(data, f"/ui/connectors?scan={result.scan_run_id}"), status_code=303)
    return result.as_dict()


@router.post("/douyin/browser/open")
async def open_douyin_browser(request: Request):
    data = await request_data(request)
    url = str(data.get("homepage_url") or data.get("url") or "https://www.douyin.com/user/self?showTab=favorite_collection").strip()
    try:
        state = await douyin_browser_collector.open(url)
    except BrowserDependencyMissing as exc:
        if wants_html(request):
            return RedirectResponse("/ui/source-setup/douyin?saved=browser-missing", status_code=303)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if wants_html(request):
        return RedirectResponse("/ui/source-setup/douyin?saved=browser-opened", status_code=303)
    return {"status": "opened", "current_url": state.current_url, "message": state.message}


@router.post("/douyin/favorites/extract")
async def extract_douyin_favorites(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    limit = parse_collection_limit(data, 10)
    should_process = truthy(data.get("process_first_ten"), True)
    try:
        items = await douyin_browser_collector.extract_visible_video_links(limit=limit)
    except DouyinPageNotReady as exc:
        if wants_html(request):
            return RedirectResponse("/ui/source-setup/douyin?saved=page-not-ready", status_code=303)
        raise HTTPException(status_code=409, detail={"message": str(exc), "diagnostics": douyin_browser_collector.diagnostics()}) from exc
    except BrowserDependencyMissing as exc:
        if wants_html(request):
            return RedirectResponse("/ui/source-setup/douyin?saved=browser-missing", status_code=303)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    transcript_failures: list[dict[str, str]] = []
    if truthy(data.get("transcribe"), True):
        cookies_file = await douyin_browser_collector.export_cookies() if hasattr(douyin_browser_collector, "export_cookies") else None
        items, transcript_failures = enrich_douyin_items_with_report(
            items,
            DouyinTranscriptService(cookies_file=cookies_file),
            limit=limit,
            require_transcript=truthy(data.get("require_transcript"), True),
        )
        if not items:
            if wants_html(request):
                return RedirectResponse("/ui/source-setup/douyin?saved=transcript-failed", status_code=303)
            raise HTTPException(status_code=422, detail={"message": "抖音收藏转写失败：没有成功生成逐字稿的条目", "failures": transcript_failures})
    connector = ensure_connector(db, "douyin", "抖音收藏夹", "browser_douyin")
    result = await SyncService(db).import_items(connector, items, "douyin_visible")
    track_event(db, "v3_task_created", {"input_type": "favorites", "source_count": result.new_count, "mode": "favorites"})
    candidate_ids = result.candidate_ids or candidate_ids_for_items(db, items)
    routed = await process_candidate_ids(db, candidate_ids, limit=10) if should_process else []
    if wants_html(request):
        return RedirectResponse(f"/ui/source-setup/douyin?saved=processed-{result.new_count}-{len(routed)}", status_code=303)
    return {**result.as_dict(), "processed": routed, "transcript_failures": transcript_failures, "transcript_failure_count": len(transcript_failures)}


@router.post("/douyin/favorites/import-items")
async def import_douyin_favorite_items(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    limit = parse_collection_limit(data, 10)
    items = build_douyin_items(data.get("items"), limit=limit)
    if not items:
        raise HTTPException(status_code=400, detail="items is required")
    transcript_failures: list[dict[str, str]] = []
    if truthy(data.get("transcribe"), True):
        items, transcript_failures = enrich_douyin_items_with_report(
            items,
            DouyinTranscriptService(),
            limit=limit,
            require_transcript=truthy(data.get("require_transcript"), True),
        )
        if not items:
            raise HTTPException(status_code=422, detail={"message": "抖音收藏转写失败：没有成功生成逐字稿的条目", "failures": transcript_failures})
    connector = ensure_connector(db, "douyin", "抖音收藏夹", "browser_douyin")
    result = await SyncService(db).import_items(connector, items, "douyin_computer_use")
    track_event(db, "v3_task_created", {"input_type": "favorites", "source_count": result.new_count, "mode": "favorites"})
    candidate_ids = result.candidate_ids or candidate_ids_for_items(db, items)
    processed = await process_candidate_ids(db, candidate_ids, limit=10) if truthy(data.get("process_first_ten"), True) else []
    return {
        **result.as_dict(),
        "candidate_ids": candidate_ids,
        "processed": processed,
        "processed_count": len(processed),
        "transcript_failures": transcript_failures,
        "transcript_failure_count": len(transcript_failures),
    }


@router.get("/connectors/{connector_id}/logs")
def connector_logs(connector_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    logs = db.query(ScanLog).filter(ScanLog.connector_id == connector_id).order_by(ScanLog.created_at.desc()).all()
    return [
        {
            "id": log.id,
            "scan_run_id": log.scan_run_id,
            "level": log.level,
            "message": log.message,
            "created_at": iso(log.created_at),
        }
        for log in logs
    ]


@router.post("/manual/idea")
async def manual_idea(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    track_event(db, "task_create_submitted", {"task_type": "manual_idea"})
    title = str(data.get("title") or "未命名想法").strip() or "未命名想法"
    content = str(data.get("content") or "").strip()
    tags = str(data.get("tags") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    item_id = f"manual-{uuid4().hex}"
    canonical_url = f"starmind://idea/{item_id}"
    candidate = CandidateItem(
        source_type="manual_idea",
        platform="手动录入",
        external_item_id=item_id,
        canonical_url=canonical_url,
        raw_url=canonical_url,
        title=title,
        content_type="note",
        metadata_json=json.dumps({"content": content, "tags": tags, "source": "manual_idea"}, ensure_ascii=False),
        status=PENDING_CLASSIFICATION,
    )
    db.add(candidate)
    db.flush()
    db.add(
        SyncLedgerItem(
            connector_id=None,
            platform="manual_idea",
            external_item_id=item_id,
            canonical_url=canonical_url,
            raw_url=canonical_url,
            scan_run_id=f"manual_{candidate.id}",
            candidate_id=candidate.id,
        )
    )
    db.commit()
    db.refresh(candidate)
    track_event(db, "task_created", {"task_type": "manual_idea"}, candidate_id=candidate.id)
    track_event(db, "v3_task_created", {"input_type": "idea", "duplicate": False}, candidate_id=candidate.id)
    if truthy(data.get("process_now"), False):
        page_type = str(data.get("page_type") or "knowledge").strip()
        if page_type not in WikiMaintenanceService.SUPPORTED_PAGE_TYPES:
            page_type = "knowledge"
        raw_source = RawSourceService(db).ingest_candidate(candidate.id)
        page = await WikiMaintenanceService(db).create_page_from_raw_source(raw_source.id, page_type=page_type)
        track_event(
            db,
            "v3_processing_completed",
            {"current_step": "idea_processed", "page_type": page_type},
            candidate_id=candidate.id,
            raw_source_id=raw_source.id,
            page_id=page.page_id,
        )
        if wants_html(request):
            return RedirectResponse(f"/ui/review/{page.page_id}?saved=manual-idea", status_code=303)
        return {
            "status": "created",
            "candidate": candidate_to_dict(candidate),
            "raw_source_id": raw_source.id,
            "wiki_page_id": page.page_id,
            "page_type": page.page_type,
        }
    if wants_html(request):
        return RedirectResponse(f"/ui/task/candidate/{candidate.id}?created=manual-idea", status_code=303)
    return {"status": "created", "candidate": candidate_to_dict(candidate)}


@router.post("/distill/profile")
async def distill_profile(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    platform = str(data.get("platform") or "社交媒体").strip()
    profile_url = str(data.get("profile_url") or "").strip()
    target_name = str(data.get("target_name") or profile_url or "未命名博主").strip()
    category = str(data.get("category") or target_name).strip()
    if not profile_url:
        raise HTTPException(status_code=400, detail="profile_url is required")
    payload = get_distill_requests()
    # Dedup: skip if same profile_url already exists
    existing_urls = {r.get("profile_url") for r in payload["requests"]}
    if profile_url in existing_urls:
        track_event(db, "v3_task_created", {"input_type": "profile", "mode": "creator", "duplicate": True})
        if wants_html(request):
            return RedirectResponse("/ui/distill", status_code=303)
        return {"status": "duplicate", "message": "该博主已在蒸馏列表中"}
    request_id = f"distill-{uuid4().hex}"
    payload["requests"].insert(
        0,
        {
            "id": request_id,
            "platform": platform,
            "target_name": target_name,
            "profile_url": profile_url,
            "category": category,
            "status": "待蒸馏",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    write_json(DISTILL_REQUESTS_PATH, payload)
    track_event(db, "v3_task_created", {"input_type": "profile", "mode": "creator", "duplicate": False})
    imported: dict[str, Any] | None = None
    if "抖音" in platform or "douyin.com/user" in profile_url:
        limit = parse_collection_limit(data, 5)
        transcript_failures: list[dict[str, str]] = []
        try:
            await douyin_browser_collector.open(douyin_profile_base_url(profile_url))
            fallback_item = douyin_profile_vid_fallback(profile_url, target_name)
            items = [fallback_item] if fallback_item else await douyin_browser_collector.extract_visible_video_links(limit=limit, require_collection_page=False)
            if truthy(data.get("transcribe"), True):
                cookies_file = await douyin_browser_collector.export_cookies() if hasattr(douyin_browser_collector, "export_cookies") else None
                items, transcript_failures = enrich_douyin_items_with_report(
                    items,
                    DouyinTranscriptService(cookies_file=cookies_file),
                    limit=limit,
                    require_transcript=truthy(data.get("require_transcript"), True),
                )
                if not items:
                    raise DouyinTranscriptError(json.dumps(transcript_failures, ensure_ascii=False))
        except (BrowserDependencyMissing, DouyinPageNotReady, DouyinTranscriptError) as exc:
            if wants_html(request):
                return RedirectResponse(html_redirect_target(data, "/ui/distill?created=distill-profile-failed"), status_code=303)
            raise HTTPException(status_code=422, detail=f"抖音博主蒸馏失败：{exc}") from exc
        connector = ensure_connector(db, "douyin", "抖音博主主页", "browser_douyin_creator")
        result = await SyncService(db).import_items(connector, items, "douyin_creator")
        candidate_ids = result.candidate_ids or candidate_ids_for_items(db, items)
        processed = await process_candidate_ids(db, candidate_ids, limit=limit or 5)
        imported = {
            **result.as_dict(),
            "candidate_ids": candidate_ids,
            "processed": processed,
            "processed_count": len(processed),
            "transcript_failures": transcript_failures,
            "transcript_failure_count": len(transcript_failures),
        }
        track_event(db, "v3_processing_completed", {"current_step": "creator_profile_import", "source_count": result.new_count})
    if wants_html(request):
        return RedirectResponse(html_redirect_target(data, "/ui/distill?created=distill-profile"), status_code=303)
    return {"status": "created", "request": payload["requests"][0], "imported": imported}


@router.get("/ui/recycle", response_class=HTMLResponse)
def recycle_page(request: Request, db: Session = Depends(get_db)):
    recycle_items = (
        db.query(RecycleBinItem)
        .filter(RecycleBinItem.status.in_(RECYCLE_STATUSES))
        .order_by(RecycleBinItem.archived_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request,
        "recycle.html",
        template_context(request, "recycle", db, recycle_items=recycle_items),
    )


@router.get("/ui/pending", response_class=HTMLResponse)
def legacy_pending_page(request: Request):
    created = request.query_params.get("created") or ""
    if created.startswith("recycle-cleared"):
        return RedirectResponse("/ui/recycle?cleared=1", status_code=303)
    return RedirectResponse("/ui/sources", status_code=303)


@router.get("/ui/sources", response_class=HTMLResponse)
def sources_page(request: Request, db: Session = Depends(get_db)):
    sources = db.query(RawSource).order_by(RawSource.created_at.desc()).all()
    raw_service = RawSourceService(db)
    for source in sources:
        candidate = db.get(CandidateItem, source.candidate_id) if source.candidate_id else None
        source.title = raw_service.display_title_for_source(source, candidate)
    favorite_sources = [source for source in sources if source.source_type not in {"passive_link", "manual_idea", "distill_profile"}]
    link_items = db.query(CandidateItem).filter(CandidateItem.source_type == "passive_link").order_by(CandidateItem.created_at.desc()).all()
    idea_items = db.query(CandidateItem).filter(CandidateItem.source_type == "manual_idea").order_by(CandidateItem.created_at.desc()).all()
    source_id = request.query_params.get("source_id")
    selected_source = db.get(RawSource, int(source_id)) if source_id and source_id.isdigit() else (sources[0] if sources else None)
    if selected_source:
        selected_candidate = db.get(CandidateItem, selected_source.candidate_id) if selected_source.candidate_id else None
        selected_source.title = raw_service.display_title_for_source(selected_source, selected_candidate)
    selected_metadata = safe_json(selected_source.metadata_json) if selected_source else {}
    selected_transcript = read_local_text(selected_source.transcript_path if selected_source else None)
    selected_raw_text = read_local_text(selected_source.raw_content_path if selected_source else None)
    if selected_source:
        selected_transcript = raw_service.normalize_transcript_heading(selected_transcript, selected_source.title)
        selected_raw_text = raw_service.normalize_transcript_heading(selected_raw_text, f"原始资料：{selected_source.title}")
    return templates.TemplateResponse(
        request,
        "sources.html",
        template_context(
            request,
            "sources",
            db,
            sources=sources,
            selected_source=selected_source,
            selected_metadata=selected_metadata,
            selected_transcript=selected_transcript,
            selected_raw_text=selected_raw_text,
            selected_source_type_label=source_type_label(selected_source.source_type if selected_source else None),
            favorite_sources=favorite_sources,
            distill_requests=get_distill_requests()["requests"],
            link_items=link_items,
            idea_items=idea_items,
        ),
    )


@router.post("/api/sources/{source_id}/delete")
async def delete_source(source_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        RecycleService(db).archive_raw_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if wants_html(request):
        return RedirectResponse("/ui/sources?deleted=1", status_code=303)
    return {"status": "deleted", "raw_source_id": source_id}


@router.post("/api/sources/batch-delete")
async def batch_delete_sources(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    raw_ids = data.get("ids", [])
    if isinstance(raw_ids, str):
        source_ids = [raw_ids]
    else:
        source_ids = [str(source_id) for source_id in raw_ids if source_id]
    service = RecycleService(db)
    deleted_count = 0
    for source_id in source_ids:
        if not str(source_id).isdigit():
            continue
        try:
            service.archive_raw_source(int(source_id))
            deleted_count += 1
        except ValueError:
            continue
    if wants_html(request):
        return RedirectResponse(f"/ui/sources?deleted={deleted_count}", status_code=303)
    return {"status": "deleted", "count": deleted_count}


@router.get("/ui/wiki", response_class=HTMLResponse)
async def wiki_page(request: Request, db: Session = Depends(get_db)):
    from app.models import WikiCategory
    from sqlalchemy import func

    track_event(db, "page_viewed", {"page": "wiki"})
    section_id = request.query_params.get("section") or "knowledge"
    active_section = next((item for item in WIKI_SECTIONS if item["id"] == section_id), WIKI_SECTIONS[0])

    # Category filtering
    category_id = request.query_params.get("category")
    active_category = db.get(WikiCategory, int(category_id)) if category_id else None

    visible_statuses = ["active", "needs_review"]
    all_pages = db.query(WikiPage).filter(WikiPage.status.in_(visible_statuses)).order_by(WikiPage.last_updated_at.desc()).all()
    query = db.query(WikiPage).filter(WikiPage.status.in_(visible_statuses)).order_by(WikiPage.last_updated_at.desc())

    if active_category:
        query = query.filter(WikiPage.category_id == active_category.id)
        pages = query.all()
    elif active_section["id"] == "index":
        pages = all_pages
    else:
        query = query.filter(WikiPage.page_type == active_section["page_type"])
        pages = query.all()

    page_id = request.query_params.get("page_id")
    selected_page = next((page for page in all_pages if page.page_id == page_id), None) if page_id else None
    if selected_page is None and pages:
        selected_page = pages[0]
    selected_refs = page_json_list(selected_page.source_refs_json if selected_page else "[]")
    selected_tags = page_tags(selected_page.tags_json if selected_page else "[]")
    source_map = {source.id: source for source in db.query(RawSource).all()}
    section_counts = {
        "knowledge": db.query(WikiPage).filter(WikiPage.status.in_(visible_statuses), WikiPage.page_type == "knowledge").count(),
        "methodology": db.query(WikiPage).filter(WikiPage.status.in_(visible_statuses), WikiPage.page_type == "methodology").count(),
        "sop": db.query(WikiPage).filter(WikiPage.status.in_(visible_statuses), WikiPage.page_type == "sop").count(),
        "skills": db.query(WikiPage).filter(WikiPage.status.in_(visible_statuses), WikiPage.page_type == "skill").count(),
        "index": len(all_pages),
    }

    # Dynamic categories with counts
    wiki_categories = []
    cat_rows = (
        db.query(WikiCategory, func.count(WikiPage.id))
        .outerjoin(WikiPage, (WikiPage.category_id == WikiCategory.id) & (WikiPage.status.in_(visible_statuses)))
        .group_by(WikiCategory.id)
        .order_by(WikiCategory.display_order)
        .all()
    )
    for category, count in cat_rows:
        wiki_categories.append(
            SimpleNamespace(
                id=category.id,
                name=category.name,
                slug=category.slug,
                count=count,
            )
        )

    selected_markdown = read_wiki_markdown(selected_page)
    wiki_question = (request.query_params.get("q") or "").strip()
    wiki_answer = None
    wiki_answer_html = ""
    if wiki_question and selected_page:
        wiki_answer = SimpleNamespace(
            run_id="local",
            provider="local",
            model="wiki-page",
            sources=[{"title": selected_page.title}],
        )
        text = selected_markdown.strip()
        excerpt = text[:900] if text else "这篇页面还没有可读取的正文。"
        wiki_answer_html = render_markdown(f"基于当前页面内容：\n\n{excerpt}")

    return templates.TemplateResponse(
        request,
        "wiki.html",
        template_context(
            request,
            "wiki",
            db,
            created=request.query_params.get("created"),
            active_section=active_section,
            active_category=active_category,
            pages=pages,
            all_pages=all_pages,
            total_page_count=len(all_pages),
            section_counts=section_counts,
            wiki_categories=wiki_categories,
            selected_page=selected_page,
            selected_refs=selected_refs,
            selected_tags=selected_tags,
            selected_markdown_html=render_markdown(selected_markdown),
            wiki_question=wiki_question,
            wiki_answer=wiki_answer,
            wiki_answer_html=wiki_answer_html,
            source_map=source_map,
        ),
    )


@router.post("/api/wiki/{page_id}/delete")
async def delete_wiki_page(page_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        RecycleService(db).archive_wiki_page(page_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if wants_html(request):
        return RedirectResponse("/ui/wiki?deleted=1", status_code=303)
    return {"status": "deleted", "page_id": page_id}


@router.post("/api/wiki/batch-delete")
async def batch_delete_wiki_pages(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    raw_page_ids = data.get("page_ids", [])
    if isinstance(raw_page_ids, str):
        page_ids = [raw_page_ids]
    else:
        page_ids = [str(page_id) for page_id in raw_page_ids if page_id]
    service = RecycleService(db)
    deleted_count = 0
    for page_id in page_ids:
        try:
            service.archive_wiki_page(page_id)
            deleted_count += 1
        except ValueError:
            continue
    if wants_html(request):
        return RedirectResponse(f"/ui/wiki?deleted={deleted_count}", status_code=303)
    return {"status": "deleted", "count": deleted_count}


@router.post("/recycle/{recycle_item_id}/restore")
async def restore_recycled_item(recycle_item_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        result = RecycleService(db).restore(recycle_item_id)
    except ValueError as exc:
        if wants_html(request):
            return RedirectResponse("/ui/recycle?error=restore", status_code=303)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if wants_html(request):
        return RedirectResponse("/ui/recycle?restored=1", status_code=303)
    if isinstance(result, WikiPage):
        return {"status": "restored", "page_id": result.page_id}
    if isinstance(result, RawSource):
        return {"status": "restored", "raw_source_id": result.id}
    if isinstance(result, CandidateItem):
        return {"status": "restored", "candidate_id": result.id}
    return {"status": "restored"}


@router.post("/recycle/{recycle_item_id}/delete")
def delete_recycled_item(recycle_item_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        RecycleService(db).permanent_delete(recycle_item_id)
    except ValueError as exc:
        if wants_html(request):
            return RedirectResponse("/ui/recycle?error=delete", status_code=303)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if wants_html(request):
        return RedirectResponse("/ui/recycle?deleted=1", status_code=303)
    return {"status": "deleted"}


@router.post("/recycle/clear-all")
def clear_recycle_bin(request: Request, db: Session = Depends(get_db)):
    db.query(RecycleBinItem).delete(synchronize_session=False)
    db.commit()
    if wants_html(request):
        return RedirectResponse("/ui/recycle?cleared=1", status_code=303)
    return {"status": "cleared"}


@router.post("/api/wiki/categories/{category_id}/rename")
async def rename_wiki_category(category_id: int, request: Request, db: Session = Depends(get_db)):
    from app.models import WikiCategory
    data = await request_data(request)
    new_name = (data.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name is required")
    cat = db.get(WikiCategory, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    cat.name = new_name
    db.commit()
    return {"id": cat.id, "name": cat.name}


@router.post("/api/wiki/categories/{category_id}/delete")
def delete_wiki_category(category_id: int, db: Session = Depends(get_db)):
    import json as _json
    from app.models import RecycleBinItem, WikiCategory, WikiPage
    cat = db.get(WikiCategory, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    # Collect page_ids that belong to this category for restore later
    page_ids = [p.page_id for p in db.query(WikiPage.page_id).filter(WikiPage.category_id == cat.id).all()]
    # Unlink pages from this category
    db.query(WikiPage).filter(WikiPage.category_id == cat.id).update(
        {"category_id": None}, synchronize_session=False
    )
    # Snapshot the category for restore
    snap = _json.dumps({"name": cat.name, "slug": cat.slug, "display_order": cat.display_order, "page_ids": page_ids})
    recycle_item = RecycleBinItem(
        item_type="wiki_category",
        title=cat.name,
        page_id=None,
        canonical_url="",
        external_item_id=str(cat.id),
        platform="wiki_category",
        raw_source_snapshot_json=snap,
        status="deleted",
    )
    db.add(recycle_item)
    db.delete(cat)
    db.commit()
    return {"ok": True}


# ─── Push scheduler API ─────────────────────────────────────────────────────


@router.get("/api/push/preferences")
def push_preferences_api(db: Session = Depends(get_db)):
    from app.models import UserPreference, WikiCategory
    cats = db.query(WikiCategory).order_by(WikiCategory.display_order).all()
    prefs = {p.domain: p.score for p in db.query(UserPreference).all()}
    return [{"category": c.name, "score": prefs.get(c.name, 50)} for c in cats]


@router.post("/api/push/preferences")
async def save_push_preferences(request: Request, db: Session = Depends(get_db)):
    from app.services.push_scheduler_service import PushSchedulerService
    data = await request_data(request)
    preferences = data.get("preferences", {})
    PushSchedulerService(db).save_preferences(preferences)
    return {"ok": True}


@router.post("/api/push/schedule")
async def save_push_schedule(request: Request, db: Session = Depends(get_db)):
    from app.services.push_scheduler_service import PushSchedulerService
    data = await request_data(request)
    days = data.get("days", [1, 2, 3, 4, 5])
    times = data.get("times")
    time = data.get("time")
    PushSchedulerService(db).save_schedule(days, times=times, time=time)
    return {"ok": True}


@router.get("/api/push/items")
async def push_items_api(db: Session = Depends(get_db)):
    """Return push items only if current time matches a scheduled push time."""
    from datetime import datetime
    from app.models import PushSettings
    settings = db.query(PushSettings).first()
    if not settings or settings.is_paused or not settings.push_time:
        return []
    now = datetime.now()
    current_day = now.isoweekday()
    current_time = now.strftime("%H:%M")
    push_days = [int(d) for d in (settings.push_days or "").split(",") if d.isdigit()]
    push_times = [t.strip() for t in (settings.push_time or "").split(",") if t.strip()]
    if current_day not in push_days or current_time not in push_times:
        return []
    # Check if we already pushed this minute (prevent double push)
    from app.models import PushHistory
    last_push = db.query(PushHistory).order_by(PushHistory.pushed_at.desc()).first()
    if last_push and last_push.pushed_at:
        last_min = last_push.pushed_at.strftime("%Y-%m-%d %H:%M")
        now_min = now.strftime("%Y-%m-%d %H:%M")
        if last_min == now_min:
            return []
    from app.services.push_scheduler_service import PushSchedulerService
    items = await PushSchedulerService(db).generate_push_items()
    return items


@router.post("/api/push/test")
async def push_test_api(db: Session = Depends(get_db)):
    """Manually trigger a push right now (for testing)."""
    from app.services.push_scheduler_service import PushSchedulerService
    items = await PushSchedulerService(db).generate_push_items()
    return items


@router.get("/api/push/pending-feedback")
def pending_feedback_api(db: Session = Depends(get_db)):
    """Legacy endpoint kept inert so cached old JS cannot show refresh-time feedback."""
    return []


@router.post("/api/push/preference-feedback")
async def push_feedback_new(request: Request, db: Session = Depends(get_db)):
    from app.services.push_scheduler_service import PushSchedulerService
    data = await request_data(request)
    push_id = int(data.get("push_history_id") or 0)
    feedback = data.get("feedback", "")
    if feedback not in ("like", "unlike"):
        raise HTTPException(status_code=400, detail="feedback must be 'like' or 'unlike'")
    result = PushSchedulerService(db).handle_feedback(push_id, feedback)
    return result


# ─── Connector sync schedule API ────────────────────────────────────────────


@router.get("/api/sync/schedule/{connector_id}")
def get_sync_schedule(connector_id: int, db: Session = Depends(get_db)):
    connector = db.get(Connector, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    cron = connector.auto_sync_cron or "0 0 * * *"
    parts = cron.split()
    # Parse cron to days + time
    minute = parts[0] if len(parts) > 0 else "0"
    hour = parts[1] if len(parts) > 1 else "0"
    dow = parts[4] if len(parts) > 4 else "*"
    days = [int(d) for d in dow.split(",") if d.isdigit()] if dow != "*" else list(range(7))
    return {
        "connector_id": connector_id,
        "enabled": connector.auto_sync_enabled,
        "days": days,
        "time": f"{int(hour):02d}:{int(minute):02d}",
        "cron": cron,
    }


@router.post("/api/sync/schedule")
async def save_sync_schedule(request: Request, db: Session = Depends(get_db)):
    from app.scheduler import register_connector_job
    data = await request_data(request)
    platform = str(data.get("platform") or "").strip()
    connector_id = int(data.get("connector_id") or 0)
    days = data.get("days", [0, 1, 2, 3, 4, 5, 6])
    time = data.get("time", "00:00")

    # Find connector by platform if connector_id not provided
    connector = None
    if connector_id:
        connector = db.get(Connector, connector_id)
    if not connector and platform:
        connector = db.query(Connector).filter(Connector.platform == platform).first()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    hour, minute = time.split(":")
    dow = ",".join(str(d) for d in sorted(days))
    cron_expr = f"{minute} {hour} * * {dow}"
    connector.auto_sync_cron = cron_expr
    connector.auto_sync_enabled = True
    db.commit()

    register_connector_job(connector.id, cron_expr)
    return {"ok": True, "cron": cron_expr}


@router.post("/api/sync/schedule/apply-all")
async def apply_schedule_to_all(request: Request, db: Session = Depends(get_db)):
    from app.scheduler import register_connector_job
    data = await request_data(request)
    source_id = int(data.get("connector_id") or 0)
    source = db.get(Connector, source_id)
    if not source or not source.auto_sync_cron:
        raise HTTPException(status_code=404, detail="Source connector not found or no schedule set")

    connectors = db.query(Connector).filter(Connector.id != source_id).all()
    for conn in connectors:
        conn.auto_sync_cron = source.auto_sync_cron
        conn.auto_sync_enabled = True
        register_connector_job(conn.id, source.auto_sync_cron)
    db.commit()
    return {"ok": True, "applied_to": len(connectors)}


# ─── Push settings UI page ──────────────────────────────────────────────────


@router.get("/ui/push-settings")
def push_settings_page(request: Request, db: Session = Depends(get_db)):
    from app.models import UserPreference, WikiCategory
    cats = db.query(WikiCategory).order_by(WikiCategory.display_order).all()
    prefs = {p.domain: p.score for p in db.query(UserPreference).all()}
    categories = [{"name": c.name, "score": prefs.get(c.name, 50)} for c in cats]
    settings = db.query(PushSettings).first()
    push_days = [int(d) for d in (settings.push_days or "1,2,3,4,5").split(",") if d.isdigit()] if settings else [1, 2, 3, 4, 5]
    push_times = (settings.push_time).split(",") if settings and settings.push_time else []
    return templates.TemplateResponse(request, "push_settings.html", {
        "request": request,
        "categories": categories,
        "push_days": push_days,
        "push_times": push_times,
    })
