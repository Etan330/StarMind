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
from app.models import CandidateItem, Connector, KnowledgeClassification, PushSettings, RawSource, RecycleBinItem, ScanEntry, ScanLog, SyncLedgerItem, WikiPage
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


def task_cards_for_history(db: Session, candidates: list[CandidateItem], classifications: dict[int, KnowledgeClassification]) -> list[dict[str, Any]]:
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
            task_cards=task_cards_for_history(db, recent_candidates, classifications),
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


@router.get("/ui/history", response_class=HTMLResponse)
def history_page(request: Request, db: Session = Depends(get_db)):
    track_event(db, "history_opened", {"page": "history"})
    candidates = db.query(CandidateItem).order_by(CandidateItem.created_at.desc()).limit(50).all()
    logs = db.query(ScanLog).order_by(ScanLog.created_at.desc()).limit(50).all()
    pages = db.query(WikiPage).order_by(WikiPage.last_updated_at.desc()).limit(30).all()
    classifications = latest_classifications(db, [candidate.id for candidate in candidates])
    return templates.TemplateResponse(
        request,
        "history.html",
        template_context(
            request,
            "history",
            db,
            candidates=candidates,
            logs=logs,
            pages=pages,
            classifications=classifications,
            task_cards=task_cards_for_history(db, candidates, classifications),
        ),
    )


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
    return RedirectResponse("/ui/pending", status_code=303)


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


@router.get("/ui/settings/model", response_class=HTMLResponse)
def model_settings_page(request: Request, db: Session = Depends(get_db)):
    settings = get_model_settings()
    profiles = get_model_profiles()
    return templates.TemplateResponse(
        request,
        "model_settings.html",
        template_context(
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
            events=events,
            event_counts=counts,
            recent_events=recent_events,
        ),
    )


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


@router.get("/ui/candidates", response_class=HTMLResponse)
def candidates_page(request: Request, db: Session = Depends(get_db)):
    return pending_page(request, db)


@router.get("/ui/recycle", response_class=HTMLResponse)
def recycle_page(request: Request, db: Session = Depends(get_db)):
    recycle_items = db.query(RecycleBinItem).order_by(RecycleBinItem.archived_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "recycle.html",
        template_context(request, "recycle", db, recycle_items=recycle_items),
    )


@router.get("/ui/pending", response_class=HTMLResponse)
def pending_page(request: Request, db: Session = Depends(get_db)):
    pending_candidates = (
        db.query(CandidateItem)
        .filter(CandidateItem.status.in_(REVIEWABLE_STATUSES))
        .order_by(CandidateItem.created_at.desc())
        .all()
    )
    raw_sources = db.query(RawSource).order_by(RawSource.created_at.desc()).all()
    recycle_items = db.query(RecycleBinItem).filter(RecycleBinItem.status.in_(RECYCLE_STATUSES)).order_by(RecycleBinItem.archived_at.desc()).all()
    selected = pending_candidates[0] if pending_candidates else None
    selected_source = raw_sources[0] if raw_sources else None
    classification_map = latest_classifications(db, [candidate.id for candidate in pending_candidates])
    return templates.TemplateResponse(
        request,
        "pending.html",
        template_context(
            request,
            "pending",
            db,
            candidates=pending_candidates,
            pending_candidates=pending_candidates,
            raw_sources=raw_sources,
            selected_candidate=selected,
            selected_source=selected_source,
            classification_map=classification_map,
            created=request.query_params.get("created"),
            recycle_items=recycle_items,
        ),
    )


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

    all_pages = db.query(WikiPage).order_by(WikiPage.last_updated_at.desc()).all()
    query = db.query(WikiPage).order_by(WikiPage.last_updated_at.desc())

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
        "knowledge": db.query(WikiPage).filter(WikiPage.page_type == "knowledge").count(),
        "methodology": db.query(WikiPage).filter(WikiPage.page_type == "methodology").count(),
        "sop": db.query(WikiPage).filter(WikiPage.page_type == "sop").count(),
        "skills": db.query(WikiPage).filter(WikiPage.page_type == "skill").count(),
        "index": len(all_pages),
    }

    # Dynamic categories with counts
    wiki_categories = []
    cat_rows = (
        db.query(WikiCategory, func.count(WikiPage.id))
        .outerjoin(WikiPage, WikiPage.category_id == WikiCategory.id)
        .group_by(WikiCategory.id)
        .order_by(WikiCategory.display_order)
        .all()
    )
    for cat, cnt in cat_rows:
        wiki_categories.append(SimpleNamespace(id=cat.id, name=cat.name, slug=cat.slug, count=cnt))

    selected_markdown = read_wiki_markdown(selected_page)
    wiki_question = request.query_params.get("q") or ""
    wiki_answer = None
    wiki_answer_html = ""
    if wiki_question and selected_page:
        track_event(db, "previous_task_reused", {"reuse_type": "page_question"}, page_id=selected_page.page_id)
        track_event(db, "v3_followup_question_clicked", {"question_type": "page_question"}, page_id=selected_page.page_id)
        contextual_question = f"请优先基于知识页《{selected_page.title}》和它的来源回答：{wiki_question}"
        wiki_answer = await AgentRunner(db).answer_question(contextual_question)
        wiki_answer_html = render_markdown(wiki_answer.answer)
        track_event(
            db,
            "query_answer_viewed",
            {"source": "suggested_question", "has_sources": bool(wiki_answer.sources)},
            page_id=selected_page.page_id,
        )
    return templates.TemplateResponse(
        request,
        "wiki.html",
        template_context(
            request,
            "wiki",
            db,
            pages=pages,
            all_pages=all_pages,
            selected_page=selected_page,
            selected_markdown=selected_markdown,
            selected_markdown_html=render_markdown(selected_markdown),
            selected_refs=selected_refs,
            selected_tags=selected_tags,
            source_map=source_map,
            wiki_sections=WIKI_SECTIONS,
            section_counts=section_counts,
            active_section=active_section,
            active_category=active_category,
            wiki_categories=wiki_categories,
            total_page_count=len(all_pages),
            agent_legion=get_agent_legion()["agents"],
            activation_rules=get_activation_rules()["rules"],
            created=request.query_params.get("created"),
            wiki_question=wiki_question,
            wiki_answer=wiki_answer,
            wiki_answer_html=wiki_answer_html,
        ),
    )


@router.get("/ui/activation", response_class=HTMLResponse)
def activation_page(request: Request, db: Session = Depends(get_db)):
    profiles = get_model_profiles()
    return templates.TemplateResponse(
        request,
        "activation.html",
        template_context(
            request,
            "activation",
            db,
            rules=get_activation_rules()["rules"],
            model_profiles=profiles["profiles"],
            pages=db.query(WikiPage).order_by(WikiPage.last_updated_at.desc()).limit(5).all(),
            sources=db.query(RawSource).order_by(RawSource.created_at.desc()).limit(5).all(),
            saved=request.query_params.get("saved"),
        ),
    )


@router.post("/activation/rules")
async def save_activation_rule(request: Request):
    data = await request_data(request)
    payload = get_activation_rules()
    rule_id = str(data.get("rule_id") or "").strip()
    rule = next((item for item in payload["rules"] if item.get("id") == rule_id), None) if rule_id else None
    rule = {
        "id": rule_id or f"activation-{uuid4().hex}",
        "name": str(data.get("name") or "未命名激活规则").strip(),
        "trigger": str(data.get("trigger") or "按需").strip(),
        "cadence": str(data.get("cadence") or "按需").strip(),
        "run_time": str(data.get("run_time") or "").strip(),
        "focus": str(data.get("focus") or "").strip(),
        "model_profile_id": str(data.get("model_profile_id") or "").strip(),
        "delivery": str(data.get("delivery") or "首页提醒").strip(),
        "limit": int(data.get("limit") or 3),
        "status": "已启用",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if rule_id:
        payload["rules"] = [rule if item.get("id") == rule_id else item for item in payload["rules"]]
        if not any(item.get("id") == rule_id for item in payload["rules"]):
            payload["rules"].insert(0, rule)
    else:
        payload["rules"].insert(0, rule)
    write_json(ACTIVATION_RULES_PATH, payload)
    if wants_html(request):
        return RedirectResponse("/ui/activation?saved=1", status_code=303)
    return {"status": "created", "rule": rule}


@router.post("/activation/rules/{rule_id}/run")
async def run_activation_rule(rule_id: str, request: Request):
    payload = get_activation_rules()
    rule = next((item for item in payload["rules"] if item.get("id") == rule_id), None)
    if rule is None:
        raise HTTPException(status_code=404, detail="activation rule not found")
    rule["last_run_at"] = datetime.now().isoformat(timespec="seconds")
    rule["status"] = "已启用"
    write_json(ACTIVATION_RULES_PATH, payload)
    if wants_html(request):
        return RedirectResponse("/ui/activation?saved=run", status_code=303)
    return {"status": "ran", "rule": rule}


@router.get("/ui/wiki/new", response_class=HTMLResponse)
def new_wiki_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "wiki_new.html",
        template_context(request, "wiki", db, created=request.query_params.get("created")),
    )


@router.post("/wiki/pages")
async def create_wiki_page(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    title = str(data.get("title") or "未命名页面").strip() or "未命名页面"
    page_type = str(data.get("page_type") or "knowledge").strip()
    if page_type not in {"knowledge", "methodology", "sop", "skill"}:
        page_type = "knowledge"
    content = str(data.get("content") or "").strip()
    page_id = f"page-{uuid4().hex}"
    markdown_path = LOCAL_DATA_DIR / "wiki" / f"{page_id}.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    page = WikiPage(
        page_id=page_id,
        page_type=page_type,
        title=title,
        markdown_path=str(markdown_path),
        source_refs_json="[]",
        tags_json=json.dumps([page_type], ensure_ascii=False),
        updated_by="user",
    )
    db.add(page)
    db.commit()
    if wants_html(request):
        section_id = "skills" if page_type == "skill" else page_type
        return RedirectResponse(f"/ui/wiki?section={section_id}&page_id={page_id}&created=page", status_code=303)
    return {"status": "created", "page_id": page_id}


@router.get("/knowledge/agents")
def knowledge_agents() -> dict[str, Any]:
    return get_agent_legion()


@router.post("/knowledge/agents")
async def create_knowledge_agent(request: Request):
    data = await request_data(request)
    name = str(data.get("name") or "").strip()
    focus = str(data.get("focus") or "").strip()
    cadence = str(data.get("cadence") or "每周").strip()
    if not name or not focus:
        raise HTTPException(status_code=400, detail="name and focus are required")
    payload = get_agent_legion()
    agent = {
        "id": f"agent-{uuid4().hex}",
        "name": name,
        "focus": focus,
        "cadence": cadence,
    }
    payload["agents"].insert(0, agent)
    write_json(AGENT_LEGION_PATH, payload)
    if wants_html(request):
        return RedirectResponse("/ui/wiki?created=agent", status_code=303)
    return {"status": "created", "agent": agent}


@router.get("/ui/query", response_class=HTMLResponse)
def query_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse("/#home-chat", status_code=303)


@router.get("/ui/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    track_event(db, "page_viewed", {"page": "settings"})
    settings = get_model_settings()
    profiles = get_model_profiles()
    tracking = TrackingService(db)
    return templates.TemplateResponse(
        request,
        "settings.html",
        template_context(
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
            event_counts=tracking.counts(),
            recent_events=tracking.recent(10),
        ),
    )


@router.get("/settings/events/export")
def export_product_events(db: Session = Depends(get_db)) -> JSONResponse:
    events = [
        {
            "id": event.id,
            "event_name": event.event_name,
            "properties": safe_json(event.properties_json),
            "candidate_id": event.candidate_id,
            "raw_source_id": event.raw_source_id,
            "page_id": event.page_id,
            "created_at": iso(event.created_at),
        }
        for event in TrackingService(db).recent(500)
    ]
    return JSONResponse({"events": events, "temporary_adapter": True})


# --- Delete RawSource (user-initiated only) ---


@router.post("/api/sources/{raw_source_id}/delete")
async def delete_raw_source(raw_source_id: int, request: Request, db: Session = Depends(get_db)):
    """User-initiated delete: moves RawSource to recycle bin, removes from knowledge base."""
    source = db.get(RawSource, raw_source_id)
    if not source:
        raise HTTPException(status_code=404, detail="RawSource not found")

    # Remove associated wiki pages
    pages = pages_for_raw_source(db, raw_source_id)
    for page in pages:
        page.status = "deleted"

    # Add to recycle bin
    existing = db.query(RecycleBinItem).filter(RecycleBinItem.canonical_url == source.canonical_url).first()
    if not existing:
        db.add(RecycleBinItem(
            candidate_id=source.candidate_id,
            canonical_url=source.canonical_url,
            external_item_id=source.external_item_id,
            title=source.title,
            platform=source.platform,
            reason="user_deleted",
        ))

    # Delete the raw source record
    db.delete(source)
    db.commit()

    track_event(db, "raw_source_deleted", {"platform": source.platform}, raw_source_id=raw_source_id)

    if wants_html(request):
        return RedirectResponse("/ui/sources?deleted=1", status_code=303)
    return {"status": "deleted", "raw_source_id": raw_source_id}


@router.post("/api/sources/batch-delete")
async def batch_delete_raw_sources(request: Request, db: Session = Depends(get_db)):
    """Delete multiple RawSource items."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        ids = data.get("ids") or []
    else:
        form = await request.form()
        ids = form.getlist("ids")
    if isinstance(ids, str):
        ids = [ids]
    if not ids:
        raise HTTPException(status_code=400, detail="ids required")

    deleted = 0
    for rid in ids:
        source = db.get(RawSource, int(rid))
        if not source:
            continue
        pages = pages_for_raw_source(db, source.id)
        for page in pages:
            page.status = "deleted"
        existing = db.query(RecycleBinItem).filter(RecycleBinItem.canonical_url == source.canonical_url).first()
        if not existing:
            db.add(RecycleBinItem(
                candidate_id=source.candidate_id,
                canonical_url=source.canonical_url,
                external_item_id=source.external_item_id,
                title=source.title,
                platform=source.platform,
                reason="user_deleted",
            ))
        db.delete(source)
        deleted += 1
    db.commit()

    if wants_html(request):
        return RedirectResponse(f"/ui/sources?deleted={deleted}", status_code=303)
    return {"status": "deleted", "count": deleted}


@router.get("/candidates")
def list_candidates(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [candidate_to_dict(candidate) for candidate in db.query(CandidateItem).order_by(CandidateItem.created_at.desc()).all()]


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    candidate = db.get(CandidateItem, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate_to_dict(candidate)


@router.post("/candidates/{candidate_id}/confirm")
async def confirm_candidate(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    candidate = db.get(CandidateItem, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    audit = ClassifierService(db).ensure_manual_skip_audit(candidate)
    raw_source = RawSourceService(db).ingest_candidate(candidate.id)
    track_event(
        db,
        "task_processing_started",
        {"step": "raw_source_created", "classification_label": audit.label},
        candidate_id=candidate.id,
        raw_source_id=raw_source.id,
    )
    track_event(db, "task_created", {"task_type": candidate.source_type, "object": "raw_source"}, candidate_id=candidate.id, raw_source_id=raw_source.id)
    track_event(db, "v3_processing_started", {"current_step": "source_saved"}, candidate_id=candidate.id, raw_source_id=raw_source.id)
    if wants_html(request):
        return RedirectResponse(f"/ui/task/candidate/{candidate.id}?created=raw-source", status_code=303)
    return {"status": "confirmed", "candidate_id": candidate_id, "raw_source_id": raw_source.id, "classification_audit_id": audit.id}


@router.post("/agent/process-candidate/{candidate_id}")
async def process_candidate(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        raw_source = RawSourceService(db).ingest_candidate(candidate_id)
        page = await WikiMaintenanceService(db).create_page_from_raw_source(raw_source.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if wants_html(request):
        return RedirectResponse(f"/ui/review/{page.page_id}?saved=processed", status_code=303)
    return {"status": "processed", "raw_source_id": raw_source.id, "wiki_page_id": page.page_id}


@router.post("/agent/classify-pending")
async def classify_pending(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    limit = int(data.get("limit") or 20)
    candidates = (
        db.query(CandidateItem)
        .filter(CandidateItem.status == PENDING_CLASSIFICATION)
        .order_by(CandidateItem.created_at.desc())
        .limit(limit)
        .all()
    )
    routed = await classify_and_route_candidates(db, [candidate.id for candidate in candidates], limit=limit)
    if wants_html(request):
        return RedirectResponse(f"/ui/pending?created=classified&count={len(routed)}", status_code=303)
    return {"status": "classified", "items": routed}


@router.post("/agent/raw-sources/{raw_source_id}/create-page")
async def create_page_from_raw_source(raw_source_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    page_type = str(data.get("page_type") or "knowledge").strip()
    force = truthy(data.get("force"), False)
    if page_type not in {"knowledge", "methodology", "sop", "skill"}:
        raise HTTPException(status_code=400, detail="Unsupported page_type")
    track_event(db, "task_processing_started", {"step": "page_generation", "page_type": page_type}, raw_source_id=raw_source_id)
    track_event(db, "v3_processing_started", {"current_step": "page_generation", "page_type": page_type}, raw_source_id=raw_source_id)
    try:
        page = await WikiMaintenanceService(db).create_page_from_raw_source(raw_source_id, page_type=page_type, force=force)
    except ValueError as exc:
        track_event(db, "task_processing_failed", {"step": "page_generation", "reason": str(exc)}, raw_source_id=raw_source_id)
        track_event(db, "v3_task_create_failed", {"reason": "page_generation_failed"}, raw_source_id=raw_source_id)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    track_event(
        db,
        "task_processing_completed",
        {"step": "page_generation", "page_type": page_type},
        raw_source_id=raw_source_id,
        page_id=page.page_id,
    )
    track_event(
        db,
        "v3_processing_completed",
        {"current_step": "page_generation", "page_type": page_type},
        raw_source_id=raw_source_id,
        page_id=page.page_id,
    )
    if wants_html(request):
        return RedirectResponse(f"/ui/review/{page.page_id}?saved={page_type}-page", status_code=303)
    return {"status": "created", "raw_source_id": raw_source_id, "wiki_page_id": page.page_id, "page_type": page_type}


@router.post("/agent/process-pending")
async def process_pending(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    limit = int(data.get("limit") or 5)
    candidates = (
        db.query(CandidateItem)
        .filter(CandidateItem.status.in_(REVIEWABLE_STATUSES))
        .order_by(CandidateItem.created_at.desc())
        .limit(limit)
        .all()
    )
    processed = []
    raw_service = RawSourceService(db)
    wiki_service = WikiMaintenanceService(db)
    for candidate in candidates:
        raw_source = raw_service.ingest_candidate(candidate.id)
        page = await wiki_service.create_page_from_raw_source(raw_source.id)
        processed.append({"candidate_id": candidate.id, "raw_source_id": raw_source.id, "wiki_page_id": page.page_id})
    if wants_html(request):
        return RedirectResponse("/ui/pending?created=batch-processed", status_code=303)
    return {"status": "processed", "items": processed}


@router.post("/candidates/{candidate_id}/skip")
async def skip_candidate(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    candidate = db.get(CandidateItem, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate.status = SKIPPED
    db.commit()
    if wants_html(request):
        return RedirectResponse("/ui/pending?created=skipped", status_code=303)
    return {"status": "skipped", "candidate_id": candidate_id}


@router.post("/candidates/{candidate_id}/recycle")
async def recycle_candidate(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    candidate = db.get(CandidateItem, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    exists = db.query(RecycleBinItem).filter(RecycleBinItem.candidate_id == candidate.id).first()
    if exists is None:
        db.add(
            RecycleBinItem(
                candidate_id=candidate.id,
                canonical_url=candidate.canonical_url,
                external_item_id=candidate.external_item_id,
                title=candidate.title,
                platform=candidate.platform,
                reason="user_recycled",
            )
        )
    candidate.status = RECYCLED
    db.commit()
    if wants_html(request):
        return RedirectResponse("/ui/pending?created=recycled", status_code=303)
    return {"status": "recycled", "candidate_id": candidate_id}


@router.post("/recycle/{recycle_item_id}/restore")
async def restore_recycled_item(recycle_item_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    target = str(data.get("target") or "review")
    try:
        candidate = RecycleService(db).restore(recycle_item_id, target=target)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raw_source_id = None
    if target == "knowledge":
        raw_source = RawSourceService(db).ingest_candidate(candidate.id)
        raw_source_id = raw_source.id
    if wants_html(request):
        return RedirectResponse("/ui/pending?created=restored", status_code=303)
    return {"status": "restored", "candidate_id": candidate.id, "candidate_status": candidate.status, "raw_source_id": raw_source_id}


@router.post("/api/recycle/clear-all")
async def clear_all_recycle(request: Request, db: Session = Depends(get_db)):
    """Permanently delete all items in recycle bin."""
    count = db.query(RecycleBinItem).count()
    db.query(RecycleBinItem).delete()
    db.commit()
    if wants_html(request):
        return RedirectResponse(f"/ui/pending?created=recycle-cleared-{count}", status_code=303)
    return {"status": "cleared", "count": count}


@router.post("/passive/link")
async def passive_link(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    raw_url = str(data.get("url") or data.get("raw_url") or "").strip()
    title = str(data.get("title") or raw_url or "Untitled passive link")
    tags = str(data.get("tags") or "").strip()
    if not raw_url:
        raise HTTPException(status_code=400, detail="url is required")
    track_event(db, "task_create_submitted", {"task_type": "passive_link"})
    normalized = normalize_url(raw_url, str(data.get("platform") or "") or None)

    def find_existing_ledger():
        return (
            db.query(SyncLedgerItem)
            .filter(
                or_(
                    (SyncLedgerItem.platform == normalized.platform) & (SyncLedgerItem.external_item_id == normalized.external_item_id),
                    SyncLedgerItem.canonical_url == normalized.canonical_url,
                )
            )
            .first()
        )

    def duplicate_payload(existing: SyncLedgerItem):
        existing_context = existing_context_for_ledger(db, existing)
        track_event(db, "duplicate_detected", {"task_type": "passive_link"}, candidate_id=existing.candidate_id)
        track_event(db, "v3_task_created", {"input_type": "link", "duplicate": True}, candidate_id=existing.candidate_id)
        if wants_html(request):
            return RedirectResponse(f"/ui/create?{duplicate_query_params(existing_context)}", status_code=303)
        return {
            "status": "duplicate",
            "canonical_url": normalized.canonical_url,
            "ledger_id": existing.id,
            "existing": {
                "candidate_id": existing_context["candidate"].id if existing_context["candidate"] else None,
                "raw_source_id": existing_context["raw_source"].id if existing_context["raw_source"] else None,
                "page_id": existing_context["latest_page"].page_id if existing_context["latest_page"] else None,
            },
        }

    existing = find_existing_ledger()
    if existing:
        return duplicate_payload(existing)

    candidate_title = title
    candidate_author = None
    candidate_content_type = None
    candidate_metadata = {"source": "passive_link", "tags": tags}
    if normalized.platform == "douyin" and truthy(data.get("transcribe"), True):
        try:
            enriched = DouyinTranscriptService().enrich_item(
                ConnectorItem(
                    raw_url=normalized.canonical_url,
                    title=title,
                    author=None,
                    platform=normalized.platform,
                    content_type="video",
                    metadata={"source": "passive_link", "tags": tags},
                ),
                require_transcript=truthy(data.get("require_transcript"), True),
            )
        except DouyinTranscriptError as exc:
            raise HTTPException(status_code=422, detail=f"抖音链接转写失败：{exc}") from exc
        normalized = normalize_url(enriched.raw_url, normalized.platform)
        existing = find_existing_ledger()
        if existing:
            return duplicate_payload(existing)
        candidate_title = enriched.title
        candidate_author = enriched.author
        candidate_content_type = enriched.content_type
        candidate_metadata = enriched.metadata
    candidate = CandidateItem(
        source_type="passive_link",
        platform=normalized.platform,
        external_item_id=normalized.external_item_id,
        canonical_url=normalized.canonical_url,
        raw_url=raw_url,
        title=candidate_title,
        author=candidate_author,
        content_type=candidate_content_type,
        metadata_json=json.dumps(candidate_metadata, ensure_ascii=False),
        status=PENDING_CLASSIFICATION,
    )
    db.add(candidate)
    db.flush()
    db.add(
        SyncLedgerItem(
            connector_id=None,
            platform=normalized.platform,
            external_item_id=normalized.external_item_id,
            canonical_url=normalized.canonical_url,
            raw_url=raw_url,
            scan_run_id=f"passive_{candidate.id}",
            candidate_id=candidate.id,
        )
    )
    db.commit()
    db.refresh(candidate)
    track_event(db, "task_created", {"task_type": "passive_link"}, candidate_id=candidate.id)
    track_event(db, "v3_task_created", {"input_type": "link", "duplicate": False}, candidate_id=candidate.id)
    if truthy(data.get("process_now"), False):
        page_type = str(data.get("page_type") or "knowledge").strip()
        if page_type not in WikiMaintenanceService.SUPPORTED_PAGE_TYPES:
            page_type = "knowledge"
        raw_source = RawSourceService(db).ingest_candidate(candidate.id)
        page = await WikiMaintenanceService(db).create_page_from_raw_source(raw_source.id, page_type=page_type)
        track_event(
            db,
            "v3_processing_completed",
            {"current_step": "link_processed", "page_type": page_type},
            candidate_id=candidate.id,
            raw_source_id=raw_source.id,
            page_id=page.page_id,
        )
        if wants_html(request):
            return RedirectResponse(f"/ui/review/{page.page_id}?saved=passive-link", status_code=303)
        return {
            "status": "created",
            "candidate": candidate_to_dict(candidate),
            "raw_source_id": raw_source.id,
            "wiki_page_id": page.page_id,
            "page_type": page.page_type,
        }
    if wants_html(request):
        return RedirectResponse(f"/ui/task/candidate/{candidate.id}?created=passive-link", status_code=303)
    return {"status": "created", "candidate": candidate_to_dict(candidate)}


@router.post("/agent/query")
async def agent_query(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    question = str(data.get("question") or data.get("q") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    profile = get_model_profile(str(data.get("model_profile") or ""))
    answer = await AgentRunner(db).answer_question(
        question,
        provider_id=profile.get("provider") if profile else None,
        model=profile.get("model") if profile else None,
        model_profile_name=profile.get("name") if profile else None,
    )
    return answer.model_dump()


# --- Chat Conversations ---


@router.get("/api/conversations")
def list_conversations(db: Session = Depends(get_db)):
    from app.models.records import ChatConversation, ChatMessage

    convos = (
        db.query(ChatConversation)
        .join(ChatMessage, ChatMessage.conversation_id == ChatConversation.id)
        .group_by(ChatConversation.id)
        .order_by(ChatConversation.updated_at.desc())
        .all()
    )
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()} for c in convos]


@router.post("/api/conversations")
def create_conversation(db: Session = Depends(get_db)):
    from app.models.records import ChatConversation, ChatMessage

    empty_convo = (
        db.query(ChatConversation)
        .outerjoin(ChatMessage, ChatMessage.conversation_id == ChatConversation.id)
        .filter(ChatMessage.id.is_(None))
        .order_by(ChatConversation.updated_at.desc())
        .first()
    )
    if empty_convo:
        return {"id": empty_convo.id, "title": empty_convo.title}

    convo = ChatConversation(id=uuid4().hex, title="新对话")
    db.add(convo)
    db.commit()
    return {"id": convo.id, "title": convo.title}


@router.get("/api/conversations/{convo_id}/messages")
def get_conversation_messages(convo_id: str, db: Session = Depends(get_db)):
    from app.models.records import ChatConversation, ChatMessage

    convo = db.query(ChatConversation).filter_by(id=convo_id).first()
    if not convo:
        raise HTTPException(status_code=404, detail="conversation not found")
    msgs = db.query(ChatMessage).filter_by(conversation_id=convo_id).order_by(ChatMessage.created_at).all()
    return [
        {"id": m.id, "role": m.role, "content": m.content, "sources": json.loads(m.sources_json), "created_at": m.created_at.isoformat()}
        for m in msgs
    ]


@router.post("/api/conversations/{convo_id}/messages")
async def send_message(convo_id: str, request: Request, db: Session = Depends(get_db)):
    from app.models.records import ChatConversation, ChatMessage
    from app.services.markdown_renderer import render_markdown

    convo = db.query(ChatConversation).filter_by(id=convo_id).first()
    if not convo:
        raise HTTPException(status_code=404, detail="conversation not found")
    data = await request_data(request)
    question = str(data.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    # Save user message
    user_msg = ChatMessage(conversation_id=convo_id, role="user", content=question)
    db.add(user_msg)

    # Update conversation title from first message
    existing_count = db.query(ChatMessage).filter_by(conversation_id=convo_id, role="user").count()
    if existing_count == 0:
        convo.title = question[:20]

    db.commit()

    # Get agent answer
    profile = get_model_profile(str(data.get("model_profile") or ""))
    answer = await AgentRunner(db).answer_question(
        question,
        provider_id=profile.get("provider") if profile else None,
        model=profile.get("model") if profile else None,
        model_profile_name=profile.get("name") if profile else None,
    )

    # Save assistant message
    assistant_msg = ChatMessage(
        conversation_id=convo_id, role="assistant", content=answer.answer, sources_json=json.dumps(answer.sources, ensure_ascii=False)
    )
    db.add(assistant_msg)
    db.commit()

    return {
        "id": assistant_msg.id,
        "role": "assistant",
        "content": answer.answer,
        "content_html": render_markdown(answer.answer),
        "sources": answer.sources,
    }


@router.delete("/api/conversations/{convo_id}")
def delete_conversation(convo_id: str, db: Session = Depends(get_db)):
    from app.models.records import ChatConversation

    convo = db.query(ChatConversation).filter_by(id=convo_id).first()
    if not convo:
        raise HTTPException(status_code=404, detail="conversation not found")
    db.delete(convo)
    db.commit()
    return {"status": "deleted"}


@router.post("/api/conversations/{convo_id}/rename")
async def rename_conversation(convo_id: str, request: Request, db: Session = Depends(get_db)):
    from app.models.records import ChatConversation
    data = await request_data(request)
    title = (data.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    convo = db.query(ChatConversation).filter_by(id=convo_id).first()
    if not convo:
        raise HTTPException(status_code=404, detail="conversation not found")
    convo.title = title
    db.commit()
    return {"status": "renamed", "title": title}


# --- CDP Status ---


@router.get("/api/cdp/status")
async def cdp_status():
    from app.connectors import cdp_proxy as _cdp
    return await _cdp.check_status()


# --- Bilibili / Xiaohongshu CDP collection ---


@router.post("/bilibili/favorites/extract")
async def extract_bilibili_favorites(request: Request, db: Session = Depends(get_db)):
    from app.connectors import bilibili_collector as _bili
    from app.connectors.cdp_proxy import CDPConnectionError as _CDPErr

    data = await request_data(request)
    limit = parse_collection_limit(data, 10)
    homepage_url = str(data.get("homepage_url") or data.get("url") or "").strip()
    favorites_url = resolve_platform_favorites_url("bilibili", homepage_url)
    try:
        items = await _bili.extract_favorites(url=favorites_url, limit=limit)
    except _CDPErr as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    connector = ensure_connector(db, "bilibili", "B站收藏夹", "browser_bilibili")
    result = await SyncService(db).import_items(connector, items, "bilibili_cdp")
    track_event(db, "v3_task_created", {"input_type": "favorites", "source_count": result.new_count, "mode": "favorites"})
    if wants_html(request):
        return RedirectResponse(f"/ui/source-setup/bilibili?saved=extracted-{result.new_count}", status_code=303)
    return result.as_dict()


@router.post("/xiaohongshu/favorites/extract")
async def extract_xiaohongshu_favorites(request: Request, db: Session = Depends(get_db)):
    from app.connectors import xiaohongshu_collector as _xhs
    from app.connectors.cdp_proxy import CDPConnectionError as _CDPErr

    data = await request_data(request)
    limit = parse_collection_limit(data, 10)
    homepage_url = str(data.get("homepage_url") or data.get("url") or "").strip()
    favorites_url = resolve_platform_favorites_url("xiaohongshu", homepage_url)
    try:
        items = await _xhs.extract_favorites(url=favorites_url, limit=limit)
    except _CDPErr as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    connector = ensure_connector(db, "xiaohongshu", "小红书收藏夹", "browser_xiaohongshu")
    result = await SyncService(db).import_items(connector, items, "xiaohongshu_cdp")
    track_event(db, "v3_task_created", {"input_type": "favorites", "source_count": result.new_count, "mode": "favorites"})
    if wants_html(request):
        return RedirectResponse(f"/ui/source-setup/xiaohongshu?saved=extracted-{result.new_count}", status_code=303)
    return result.as_dict()


# --- Onboarding ---


@router.get("/api/onboarding/status")
def onboarding_status_api(db: Session = Depends(get_db)):
    from app.models import OnboardingStatus
    status = db.query(OnboardingStatus).first()
    if not status:
        return {"current_step": 0, "completed": False, "skipped": False}
    return {"current_step": status.current_step, "completed": status.completed_at is not None, "skipped": status.skipped}


@router.post("/api/onboarding/advance")
async def onboarding_advance(request: Request, db: Session = Depends(get_db)):
    from app.models import OnboardingStatus
    data = await request_data(request)
    step = int(data.get("step") or 1)
    status = db.query(OnboardingStatus).first()
    if not status:
        status = OnboardingStatus()
        db.add(status)
    status.current_step = step
    if step >= 6:
        status.completed_at = datetime.now()
    db.commit()
    return {"current_step": status.current_step, "completed": status.completed_at is not None}


@router.post("/api/onboarding/skip")
async def onboarding_skip(db: Session = Depends(get_db)):
    from app.models import OnboardingStatus
    status = db.query(OnboardingStatus).first()
    if not status:
        status = OnboardingStatus()
        db.add(status)
    status.skipped = True
    db.commit()
    return {"skipped": True}


@router.post("/api/onboarding/reset")
async def onboarding_reset(db: Session = Depends(get_db)):
    from app.models import OnboardingStatus
    status = db.query(OnboardingStatus).first()
    if status:
        status.current_step = 0
        status.completed_at = None
        status.skipped = False
        db.commit()
    return {"current_step": 0, "completed": False, "skipped": False}


# --- Preferences ---


@router.get("/api/preferences")
def list_preferences(db: Session = Depends(get_db)):
    from app.models import UserPreference
    prefs = db.query(UserPreference).order_by(UserPreference.score.desc()).all()
    return [{"domain": p.domain, "score": p.score} for p in prefs]


@router.post("/api/preferences")
async def save_preference(request: Request, db: Session = Depends(get_db)):
    from app.models import UserPreference
    data = await request_data(request)
    domain = str(data.get("domain") or "").strip()
    score = max(0, min(100, int(data.get("score") or 50)))
    if not domain:
        raise HTTPException(status_code=400, detail="domain is required")
    pref = db.query(UserPreference).filter(UserPreference.domain == domain).first()
    if pref:
        pref.score = score
    else:
        db.add(UserPreference(domain=domain, score=score))
    db.commit()
    return {"domain": domain, "score": score}


# --- Push ---


@router.get("/api/push/settings")
def push_settings_api(db: Session = Depends(get_db)):
    from app.models import PushSettings
    s = db.query(PushSettings).first()
    if not s:
        return {"start_time": "08:00", "end_time": "22:00", "frequency_hours": 4, "items_per_push": 3, "is_paused": False}
    return {"start_time": s.start_time, "end_time": s.end_time, "frequency_hours": s.frequency_hours, "items_per_push": s.items_per_push, "is_paused": s.is_paused}


@router.post("/api/push/settings")
async def save_push_settings(request: Request, db: Session = Depends(get_db)):
    from app.models import PushSettings
    data = await request_data(request)
    s = db.query(PushSettings).first()
    if not s:
        s = PushSettings()
        db.add(s)
    s.start_time = str(data.get("start_time") or "08:00")
    s.end_time = str(data.get("end_time") or "22:00")
    s.frequency_hours = int(data.get("frequency_hours") or 4)
    s.items_per_push = int(data.get("items_per_push") or 3)
    s.is_paused = truthy(data.get("is_paused"), False)
    db.commit()
    return {"status": "saved"}


@router.get("/api/push/current")
async def push_current(db: Session = Depends(get_db)):
    from app.services.push_service import PushService
    items = await PushService(db).generate_push()
    return {"items": items}


@router.post("/api/push/feedback")
async def push_feedback(request: Request, db: Session = Depends(get_db)):
    from app.services.push_service import PushService
    data = await request_data(request)
    push_id = int(data.get("push_id") or 0)
    feedback = str(data.get("feedback") or "").strip()
    if not push_id or feedback not in {"like", "unlike"}:
        raise HTTPException(status_code=400, detail="push_id and feedback (like/unlike) required")
    await PushService(db).handle_feedback(push_id, feedback)
    return {"status": "recorded"}


# --- Knowledge Graph ---


@router.get("/api/graph")
def graph_api(request: Request, db: Session = Depends(get_db)):
    from app.services.graph_service import GraphService
    domain_filter = request.query_params.get("domain")
    return GraphService(db).get_graph_data(domain_filter=domain_filter)


@router.get("/api/graph/node/{raw_source_id}")
def graph_node_detail(raw_source_id: int, db: Session = Depends(get_db)):
    from app.services.graph_service import GraphService
    return GraphService(db).get_node_detail(raw_source_id)


# --- Batch title classification ---


@router.post("/api/sync/scan-titles")
async def scan_titles(request: Request, db: Session = Depends(get_db)):
    from app.connectors import bilibili_collector, xiaohongshu_collector, cdp_proxy as _cdp
    from app.connectors.cdp_proxy import CDPConnectionError as _CDPErr

    data = await request_data(request)
    platform = str(data.get("platform") or "").strip()
    limit = parse_collection_limit(data, default=10)
    homepage_url = str(data.get("homepage_url") or data.get("url") or "").strip()

    # 已入库去重集（仅用于「全部已入库」UX 提示，按 note_id 匹配成功入库的条目）
    def _extract_note_id(url: str) -> str:
        path = str(url or "").split('?')[0].rstrip('/')
        return path.split('/')[-1]

    existing_note_ids = set()
    for ledger in db.query(SyncLedgerItem).filter(
        SyncLedgerItem.platform == platform,
        SyncLedgerItem.raw_source_id != None,  # noqa: E711
    ).all():
        existing_note_ids.add(_extract_note_id(ledger.raw_url))
        existing_note_ids.add(_extract_note_id(ledger.canonical_url))
    for rs in db.query(RawSource).filter(RawSource.platform == platform).all():
        existing_note_ids.add(_extract_note_id(rs.canonical_url))
        existing_note_ids.add(_extract_note_id(rs.source_url))
    existing_note_ids.discard('')

    # 单次采集：collector 内部已做滚动累积 + 跨快照去重（_scroll_and_collect），无需在此重复滚动循环。
    try:
        if platform == "bilibili":
            favorites_url = resolve_platform_favorites_url("bilibili", homepage_url)
            items = await bilibili_collector.extract_favorites(url=favorites_url, limit=limit)
        elif platform == "xiaohongshu":
            favorites_url = resolve_platform_favorites_url("xiaohongshu", homepage_url)
            items = await xiaohongshu_collector.extract_favorites(url=favorites_url, limit=limit)
        elif platform == "douyin":
            if homepage_url:
                await douyin_browser_collector.open(homepage_url)
            items = await douyin_browser_collector.extract_visible_video_links(limit=limit, require_collection_page=False)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    except _CDPErr as exc:
        raise HTTPException(status_code=503, detail={"code": "cdp_proxy_error", "message": str(exc)}) from exc
    except BrowserDependencyMissing as exc:
        raise HTTPException(status_code=503, detail={"code": "browser_missing", "message": str(exc)}) from exc
    except DouyinPageNotReady as exc:
        raise HTTPException(status_code=428, detail={"code": "platform_page_not_ready", "message": str(exc)}) from exc

    # 「全部已入库」UX 提示：本次扫到的条目里，扣掉已成功入库的，看是否一条新的都没有。
    fresh_count = sum(
        1 for i in items
        if _extract_note_id(i.raw_url) not in existing_note_ids
    )
    total_scanned = len(items)
    all_duplicates = total_scanned > 0 and fresh_count == 0

    # 落库（DB 权威源）：历史 vs 新增切分 + 跨天去重 + 回传 scan_entry_id/已分类/已提取状态。
    svc = ScanEntryService(db)
    kind = svc.determine_kind(platform)
    if kind == "incremental":
        items = svc.filter_incremental(platform, items)
    scan_run_id = f"scan_titles_{uuid4().hex[:8]}"
    entries = svc.upsert_from_items(platform, items, kind, scan_run_id)
    if kind == "history":
        svc.record_boundary(platform, items)

    return {
        "items": entries,
        "total": len(entries),
        "collection_kind": kind,
        "total_scanned": total_scanned,
        "all_duplicates": all_duplicates,
        "message": "收藏夹内容均已入库，请先在平台新增收藏后再扫描" if all_duplicates else "",
        "login_required": False,
    }


@router.get("/api/sync/scan-entries")
async def list_scan_entries(request: Request, db: Session = Depends(get_db)):
    """刷新/换设备恢复：返回该平台（可选 kind）已落库的 ScanEntry（DB 权威源）。"""
    platform = str(request.query_params.get("platform") or "").strip()
    if not platform:
        raise HTTPException(status_code=400, detail="platform required")
    kind = str(request.query_params.get("kind") or "").strip() or None
    svc = ScanEntryService(db)
    entries = svc.list_entries(platform, kind)
    return {
        "items": entries,
        "total": len(entries),
        "collection_kind": kind,
        "history_saved": svc.is_history_saved(platform),
    }


@router.post("/api/sync/save-history")
async def save_history_favorites(request: Request, db: Session = Depends(get_db)):
    """历史「采集一次保存」：翻 history_saved=True，重入历史 Tab 走只读模式。

    无需单独 bulk-save——扫描已把每条 collection_kind=history 落库、分类已经 apply_classification 落库，
    保存只翻 flag + 前端重渲染只读（保存全部已分类条目，忽略勾选）。
    """
    data = await request_data(request)
    platform = str(data.get("platform") or "").strip()
    if not platform:
        raise HTTPException(status_code=400, detail="platform required")
    if not any(item["platform"] == platform for item in PLATFORM_PRESETS):
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    svc = ScanEntryService(db)
    svc.set_history_saved(platform, True)
    history_count = len(svc.list_entries(platform, "history"))
    return {"status": "saved", "history_saved": True, "history_count": history_count}


@router.post("/api/sync/reset-history")
async def reset_history_favorites(request: Request, db: Session = Depends(get_db)):
    """「重新扫描历史」：清 history_saved + first_scan_done，使下次扫描重新走 history 全量。"""
    data = await request_data(request)
    platform = str(data.get("platform") or "").strip()
    if not platform:
        raise HTTPException(status_code=400, detail="platform required")
    if not any(item["platform"] == platform for item in PLATFORM_PRESETS):
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    ScanEntryService(db).reset_history(platform)
    return {"status": "reset", "history_saved": False}


@router.post("/api/classify/batch-titles")
async def classify_batch_titles(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    items = data.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="items required")
    result = await ClassifierService(db).batch_classify_titles(items)
    # 回写分类结果到 ScanEntry（前端 items 透传 scan_entry_id，分类器原样保留在各 group.items 里）。
    # 纯函数分类器不动，回写在路由层。
    if isinstance(result, dict):
        flat: list[dict] = []
        for group in result.get("groups") or []:
            flat.extend(group.get("items") or [])
        if flat:
            ScanEntryService(db).apply_classification(flat)
    return result


@router.post("/api/sync/prepare-selected")
async def prepare_selected_items(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    platform = str(data.get("platform") or "unknown").strip()
    selected_items = data.get("selected_items") or data.get("selected") or []
    skipped_items = data.get("skipped_items") or data.get("skipped") or []
    if not isinstance(selected_items, list) or not isinstance(skipped_items, list):
        raise HTTPException(status_code=400, detail="selected_items and skipped_items must be arrays")

    connector = ensure_connector(db, platform, f"{platform} 收藏夹", f"browser_{platform}")
    connector_items = [
        connector_item_from_filter_item(item, platform)
        for item in selected_items
        if str(item.get("url") or item.get("raw_url") or "").strip()
    ]
    reusable_candidate_ids = candidate_ids_for_items(db, connector_items)
    reusable_candidates = db.query(CandidateItem).filter(CandidateItem.id.in_(reusable_candidate_ids)).all() if reusable_candidate_ids else []
    reusable_urls = {candidate.raw_url for candidate in reusable_candidates} | {candidate.canonical_url for candidate in reusable_candidates}
    new_connector_items = []
    for item in connector_items:
        normalized = normalize_url(item.raw_url, item.platform)
        if item.raw_url in reusable_urls or normalized.canonical_url in reusable_urls:
            continue
        new_connector_items.append(item)
    result = await SyncService(db).import_items(connector, new_connector_items, f"{platform}_selected")
    merged_candidate_ids = list(dict.fromkeys([*reusable_candidate_ids, *result.candidate_ids]))
    result.candidate_ids = merged_candidate_ids

    for candidate_id in result.candidate_ids:
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate_id).first()
        if ledger:
            ledger.classification_label = "knowledge_selected"

    # 把 candidate_id 回填到对应 ScanEntry（勾选提取的串联点）。
    scan_svc = ScanEntryService(db)
    for candidate_id in result.candidate_ids:
        scan_svc.link_candidate(platform, candidate_id)

    for item in skipped_items:
        raw_url = str(item.get("url") or item.get("raw_url") or "").strip()
        if not raw_url:
            continue
        normalized = normalize_url(raw_url, platform)
        existing = db.query(SyncLedgerItem).filter(
            SyncLedgerItem.platform == normalized.platform,
            SyncLedgerItem.external_item_id == normalized.external_item_id,
        ).first()
        if existing:
            existing.classification_label = existing.classification_label or "user_skipped"
            continue
        db.add(
            SyncLedgerItem(
                connector_id=connector.id,
                platform=normalized.platform,
                external_item_id=normalized.external_item_id,
                canonical_url=normalized.canonical_url,
                raw_url=raw_url,
                scan_run_id=f"user_skipped_{uuid4().hex[:8]}",
                classification_label="user_skipped",
            )
        )
    db.commit()
    return {
        "status": "prepared",
        "selected_count": len(connector_items),
        "skipped_count": len(skipped_items),
        **result.as_dict(),
    }


@router.post("/api/sync/confirm-categories")
async def confirm_categories(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    selected_domains = data.get("selected_domains") or []
    items = data.get("items") or []
    platform = str(data.get("platform") or "unknown")

    selected_items = [i for i in items if i.get("domain") in selected_domains]
    skipped_items = [i for i in items if i.get("domain") not in selected_domains]

    # Write selected items as candidates
    connector = ensure_connector(db, platform, f"{platform} 收藏夹", f"browser_{platform}")
    connector_items = [
        connector_item_from_filter_item(i, platform)
        for i in selected_items
    ]
    result = await SyncService(db).import_items(connector, connector_items, f"{platform}_category_confirmed")
    for candidate_id in result.candidate_ids:
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate_id).first()
        if ledger:
            ledger.classification_label = "knowledge_selected"

    # Write skipped items to ledger only
    for item in skipped_items:
        normalized = normalize_url(item["url"], platform)
        existing = db.query(SyncLedgerItem).filter(
            SyncLedgerItem.platform == normalized.platform,
            SyncLedgerItem.external_item_id == normalized.external_item_id,
        ).first()
        if not existing:
            db.add(SyncLedgerItem(
                platform=normalized.platform,
                external_item_id=normalized.external_item_id,
                canonical_url=normalized.canonical_url,
                raw_url=item["url"],
                scan_run_id=f"user_skipped_{uuid4().hex[:8]}",
                classification_label="user_skipped",
            ))
    db.commit()

    return {"status": "confirmed", "selected_count": len(selected_items), "skipped_count": len(skipped_items), **result.as_dict()}


# --- Doubao / Xiaohongshu Diandian Extract Selected Candidates ---


XIAOHONGSHU_NOTE_ID_RE = re.compile(r"[a-f0-9]{12,}", re.IGNORECASE)


def xiaohongshu_note_id_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    parts = [part for part in parsed.path.split("/") if part]
    for part in reversed(parts):
        if XIAOHONGSHU_NOTE_ID_RE.fullmatch(part):
            return part
    return ""


def xiaohongshu_share_url_from_candidate(candidate: CandidateItem, metadata: dict[str, Any]) -> str:
    share_url = str(metadata.get("xiaohongshu_share_url") or "").strip()
    if share_url:
        return share_url
    source_url = str(candidate.raw_url or candidate.canonical_url or "")
    note_id = str(metadata.get("xiaohongshu_note_id") or "").strip() or xiaohongshu_note_id_from_url(source_url)
    if not note_id:
        return source_url
    parsed = urlparse(source_url)
    source_query = parse_qs(parsed.query)
    params = {"source": "webshare", "xhsshare": "pc_web"}
    token = (source_query.get("xsec_token") or [""])[0]
    if token:
        params["xsec_token"] = token
    params["xsec_source"] = "pc_share"
    return f"https://www.xiaohongshu.com/discovery/item/{note_id}?{urlencode(params)}"


def xiaohongshu_share_text_for_candidate(candidate: CandidateItem) -> tuple[str, str]:
    metadata = safe_json(candidate.metadata_json)
    share_url = xiaohongshu_share_url_from_candidate(candidate, metadata)
    share_text = str(metadata.get("xiaohongshu_share_text") or "").strip()
    if share_text:
        return share_text, share_url or str(candidate.raw_url or candidate.canonical_url or "")
    title = str(candidate.title or "未识别标题").strip() or "未识别标题"
    if share_url:
        return f"【{title} | 小红书 - 你的生活兴趣社区】 {share_url}", share_url
    return title, str(candidate.raw_url or candidate.canonical_url or "")


@router.post("/api/xiaohongshu/diandian/extract-selected")
async def extract_selected_with_xiaohongshu_diandian(request: Request, db: Session = Depends(get_db)):
    from app.connectors.cdp_proxy import CDPConnectionError as _CDPErr
    from app.connectors.xiaohongshu_diandian_extractor import XiaohongshuDiandianExtractor

    data = await request_data(request)
    candidate_ids = data.get("candidate_ids") or []
    if not isinstance(candidate_ids, list) or not candidate_ids:
        raise HTTPException(status_code=400, detail="candidate_ids required")
    per_item_timeout = int(data.get("per_item_timeout_seconds") or 240)
    generate_wiki_draft = truthy(data.get("generate_wiki_draft"), False)
    switch_every, delay_min, delay_max = _pacing_params(data)

    job_id = str(data.get("job_id") or "").strip() or uuid4().hex
    pending = _filter_pending_candidates(db, candidate_ids, "xiaohongshu_diandian")
    job = DOUBAO_EXTRACT_JOBS.setdefault(
        job_id,
        {
            "platform": "xiaohongshu_diandian",
            "candidate_ids": [int(c) for c in candidate_ids if str(c).strip()],
            "status": "running",
            "paused_reason": None,
            "done_ids": [],
            "success_count": 0,
            "failed_count": 0,
            "items": [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    job["status"] = "running"
    job["paused_reason"] = None

    extractor = XiaohongshuDiandianExtractor()
    keep_diandian_tab_open = False
    not_ready_detail = {
        "code": "xiaohongshu_diandian_not_ready",
        "message": "小红书点点页面未就绪。请确认浏览器仍登录小红书，并打开 https://www.xiaohongshu.com/ai_chat 后重试。",
        "action": "open_xiaohongshu_diandian_then_retry",
        "login_url": "https://www.xiaohongshu.com/ai_chat",
    }
    try:
        try:
            ready = await extractor.check_ready()
        except _CDPErr as exc:
            raise HTTPException(status_code=503, detail=f"CDP Proxy 未连接: {exc}") from exc
        if not ready:
            keep_diandian_tab_open = True
            raise HTTPException(status_code=428, detail=not_ready_detail)

        raw_service = RawSourceService(db)
        wiki_service = WikiMaintenanceService(db)
        items: list[dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        paused = False
        for i, candidate_id in enumerate(pending):
            if i > 0 and i % switch_every == 0:
                try:
                    await extractor.start_new_conversation()
                except Exception:
                    pass
            candidate = db.get(CandidateItem, candidate_id)
            if candidate is None:
                failed_count += 1
                items.append({"candidate_id": candidate_id, "success": False, "error": "candidate_not_found"})
                continue
            # Skip already extracted (dedup)
            c_meta = safe_json(candidate.metadata_json)
            if c_meta.get("xiaohongshu_diandian_extracted") is True:
                items.append({"candidate_id": candidate_id, "success": True, "error": None, "skipped": True})
                success_count += 1
                continue
            share_text, share_url = xiaohongshu_share_text_for_candidate(candidate)
            try:
                result = await extractor.extract_content(
                    share_text=share_text,
                    url=share_url or candidate.raw_url or candidate.canonical_url,
                    content_type=candidate.content_type or "note",
                    timeout_seconds=per_item_timeout,
                )
            except _CDPErr as exc:
                failed_count += 1
                items.append({"candidate_id": candidate.id, "success": False, "error": str(exc)})
                continue

            attempts = int(getattr(result, "attempts", 1) or 1)
            retried = bool(getattr(result, "retried", False))
            if not result.success or not str(result.transcript or result.text_content or "").strip():
                # 人机验证：保留进度、暂停、break，由前端续跑。
                if result.error == "xiaohongshu_diandian_human_verification_required":
                    keep_diandian_tab_open = True
                    paused = True
                    break
                if result.error == "xiaohongshu_diandian_not_ready":
                    keep_diandian_tab_open = True
                    paused = True
                    job["paused_reason"] = "not_ready"
                    break
                failed_count += 1
                metadata = safe_json(candidate.metadata_json)
                metadata.update(
                    {
                        "xiaohongshu_diandian_extracted": False,
                        "xiaohongshu_diandian_share_text": share_text,
                        "xiaohongshu_diandian_share_url": share_url,
                        "xiaohongshu_diandian_attempts": attempts,
                        "xiaohongshu_diandian_retried": retried,
                        "xiaohongshu_diandian_error": result.error or "empty_response",
                    }
                )
                candidate.metadata_json = json.dumps(metadata, ensure_ascii=False)
                ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).first()
                if ledger:
                    ledger.classification_label = "xiaohongshu_diandian_failed"
                db.commit()
                items.append({"candidate_id": candidate.id, "success": False, "error": result.error or "empty_response", "attempts": attempts, "retried": retried})
                if i < len(pending) - 1:
                    await asyncio.sleep(random.uniform(delay_min, delay_max))
                continue

            content = str(result.transcript or result.text_content).strip()
            metadata = safe_json(candidate.metadata_json)
            prompt = str(getattr(result, "prompt", "") or "")
            metadata.update(
                {
                    "transcript": content,
                    "content": content,
                    "page_text": content,
                    "xiaohongshu_diandian_extracted": True,
                    "xiaohongshu_diandian_prompt": prompt,
                    "xiaohongshu_diandian_share_text": share_text,
                    "xiaohongshu_diandian_share_url": share_url,
                    "xiaohongshu_diandian_extracted_at": datetime.now().isoformat(timespec="seconds"),
                    "xiaohongshu_diandian_response_length": len(content),
                    "xiaohongshu_diandian_elapsed_seconds": getattr(result, "elapsed_seconds", None),
                    "xiaohongshu_diandian_attempts": attempts,
                    "xiaohongshu_diandian_retried": retried,
                    "xiaohongshu_diandian_error": None,
                }
            )
            candidate.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
            raw_source = raw_service.ingest_candidate(candidate.id)
            ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).first()
            if ledger:
                ledger.classification_label = "knowledge"
                ledger.raw_source_id = raw_source.id
                db.commit()
            ScanEntryService(db).mark_extracted(candidate.id, raw_source.id)
            wiki_page_id = None
            if generate_wiki_draft:
                page = await wiki_service.create_page_from_raw_source(raw_source.id)
                wiki_page_id = page.page_id
            success_count += 1
            job["done_ids"].append(candidate.id)
            items.append({"candidate_id": candidate.id, "success": True, "raw_source_id": raw_source.id, "wiki_page_id": wiki_page_id, "attempts": attempts, "retried": retried, "error": None})
            if i < len(pending) - 1:
                await asyncio.sleep(random.uniform(delay_min, delay_max))
    finally:
        await extractor.close(close_tab=not keep_diandian_tab_open)

    job["success_count"] = job.get("success_count", 0) + success_count
    job["failed_count"] = job.get("failed_count", 0) + failed_count
    job["items"].extend(items)

    if paused:
        remaining = _filter_pending_candidates(db, job["candidate_ids"], "xiaohongshu_diandian")
        reason = job.get("paused_reason") or "human_verification"
        job["status"] = "paused"
        job["paused_reason"] = reason
        message = (
            "检测到小红书点点人机验证。请在已打开的点点页面完成验证，然后点「我已完成，继续」继续提取剩余条目。"
            if reason == "human_verification"
            else "小红书点点页面未就绪。请在点点页面重新登录/打开后，点「我已完成，继续」继续提取剩余条目。"
        )
        return {
            "status": "paused",
            "job_id": job_id,
            "reason": reason,
            "message": message,
            "success_count": success_count,
            "failed_count": failed_count,
            "done": len(job["done_ids"]),
            "pending_remaining": len(remaining),
            "items": items,
        }

    job["status"] = "completed"
    return {"status": "completed", "job_id": job_id, "success_count": success_count, "failed_count": failed_count, "items": items}


@router.post("/api/doubao/extract-selected")
async def extract_selected_with_doubao(request: Request, db: Session = Depends(get_db)):
    from app.connectors.cdp_proxy import CDPConnectionError as _CDPErr
    from app.connectors.doubao_extractor import DoubaoExtractor

    data = await request_data(request)
    candidate_ids = data.get("candidate_ids") or []
    if not isinstance(candidate_ids, list) or not candidate_ids:
        raise HTTPException(status_code=400, detail="candidate_ids required")
    per_item_timeout = int(data.get("per_item_timeout_seconds") or 240)
    generate_wiki_draft = truthy(data.get("generate_wiki_draft"), False)
    switch_every, delay_min, delay_max = _pacing_params(data)

    # 断点续跑：带 job_id 则复用，否则新建。无论新建/续跑，pending 都由
    # already_extracted（metadata 标记）过滤——已完成的 candidate 直接跳过，幂等。
    job_id = str(data.get("job_id") or "").strip() or uuid4().hex
    pending = _filter_pending_candidates(db, candidate_ids, "doubao")
    job = DOUBAO_EXTRACT_JOBS.setdefault(
        job_id,
        {
            "platform": "doubao",
            "candidate_ids": [int(c) for c in candidate_ids if str(c).strip()],
            "status": "running",
            "paused_reason": None,
            "done_ids": [],
            "success_count": 0,
            "failed_count": 0,
            "items": [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    job["status"] = "running"
    job["paused_reason"] = None

    extractor = DoubaoExtractor()
    keep_doubao_tab_open = False
    try:
        try:
            logged_in = await extractor.check_login()
        except _CDPErr as exc:
            raise HTTPException(status_code=503, detail=f"CDP Proxy 未连接: {exc}") from exc
        login_required_detail = {
            "code": "doubao_login_required",
            "message": "需要先登录豆包。系统已打开豆包页面，请在浏览器完成登录；如果豆包没有主动弹窗，请在豆包页面发送任意一句话触发登录弹窗，登录完成后回到这里重试。豆包登录入口：https://www.doubao.com",
            "action": "open_doubao_and_login_then_retry",
            "login_url": "https://www.doubao.com",
        }
        if not logged_in:
            keep_doubao_tab_open = True
            raise HTTPException(status_code=428, detail=login_required_detail)

        raw_service = RawSourceService(db)
        wiki_service = WikiMaintenanceService(db)
        items: list[dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        paused = False
        for i, candidate_id in enumerate(pending):
            # 每 switch_every 条换新对话窗口（反爬节流，推迟人机验证出现）。
            if i > 0 and i % switch_every == 0:
                try:
                    await extractor.start_new_conversation()
                except Exception:
                    pass
            candidate = db.get(CandidateItem, candidate_id)
            if candidate is None:
                failed_count += 1
                items.append({"candidate_id": candidate_id, "success": False, "error": "candidate_not_found"})
                continue
            try:
                result = await extractor.extract_content(
                    candidate.raw_url or candidate.canonical_url,
                    candidate.content_type or "auto",
                    timeout_seconds=per_item_timeout,
                )
            except _CDPErr as exc:
                failed_count += 1
                items.append({"candidate_id": candidate.id, "success": False, "error": str(exc)})
                continue

            if not result.success or not str(result.transcript or result.text_content or "").strip():
                # 人机验证：不中断、不报错码 428，保留已完成进度，置 paused 并 break，
                # 由前端弹窗提示用户手动验证后带 job_id 重 POST 续跑。
                if result.error == "doubao_human_verification_required":
                    keep_doubao_tab_open = True
                    paused = True
                    break
                if result.error == "doubao_login_required":
                    # 新建批次首条登录失效 → 保留现状 428（让前端走登录引导）。
                    # 批中途（已成功提取过至少一条）→ 走暂停续跑路径，保住已完成进度。
                    keep_doubao_tab_open = True
                    if success_count > 0:
                        paused = True
                        job["paused_reason"] = "login_required"
                        break
                    raise HTTPException(status_code=428, detail=login_required_detail)
                failed_count += 1
                metadata = safe_json(candidate.metadata_json)
                metadata.update(
                    {
                        "doubao_extracted": False,
                        "doubao_error": result.error or "empty_response",
                        "doubao_prompt": str(getattr(result, "prompt", "") or ""),
                        "doubao_elapsed_seconds": getattr(result, "elapsed_seconds", None),
                    }
                )
                candidate.metadata_json = json.dumps(metadata, ensure_ascii=False)
                ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).first()
                if ledger:
                    ledger.classification_label = "doubao_failed"
                db.commit()
                items.append({"candidate_id": candidate.id, "success": False, "error": result.error or "empty_response"})
                # 失败条之间也节流，避免快速重试触发反爬。
                if i < len(pending) - 1:
                    await asyncio.sleep(random.uniform(delay_min, delay_max))
                continue

            content = str(result.transcript or result.text_content).strip()
            metadata = safe_json(candidate.metadata_json)
            prompt = str(getattr(result, "prompt", "") or "")
            metadata.update(
                {
                    "transcript": content,
                    "content": content,
                    "page_text": content,
                    "doubao_extracted": True,
                    "doubao_error": None,
                    "doubao_prompt": prompt,
                    "doubao_extracted_at": datetime.now().isoformat(timespec="seconds"),
                    "doubao_response_length": len(content),
                    "doubao_elapsed_seconds": getattr(result, "elapsed_seconds", None),
                    "doubao_error": None,
                }
            )
            candidate.metadata_json = json.dumps(metadata, ensure_ascii=False)
            candidate.title = result.title or candidate.title
            db.commit()
            raw_source = raw_service.ingest_candidate(candidate.id)
            ScanEntryService(db).mark_extracted(candidate.id, raw_source.id)
            wiki_page_id = None
            if generate_wiki_draft:
                page = await wiki_service.create_page_from_raw_source(raw_source.id)
                wiki_page_id = page.page_id
            success_count += 1
            job["done_ids"].append(candidate.id)
            items.append(
                {
                    "candidate_id": candidate.id,
                    "success": True,
                    "raw_source_id": raw_source.id,
                    "wiki_page_id": wiki_page_id,
                }
            )
            # 成功条之间随机延时（最后一条不必等）。
            if i < len(pending) - 1:
                await asyncio.sleep(random.uniform(delay_min, delay_max))
    finally:
        await extractor.close(close_tab=not keep_doubao_tab_open)

    job["success_count"] = job.get("success_count", 0) + success_count
    job["failed_count"] = job.get("failed_count", 0) + failed_count
    job["items"].extend(items)

    if paused:
        # 暂停未完成的剩余（含当前这条未成功的）由 metadata 标记决定，续跑时重新过滤。
        remaining = _filter_pending_candidates(db, job["candidate_ids"], "doubao")
        reason = job.get("paused_reason") or "human_verification"
        job["status"] = "paused"
        job["paused_reason"] = reason
        message = (
            "检测到豆包人机验证。请在已打开的豆包页面完成验证，然后点「我已完成，继续」继续提取剩余条目。"
            if reason == "human_verification"
            else "豆包登录状态失效。请在豆包页面重新登录后，点「我已完成，继续」继续提取剩余条目。"
        )
        return {
            "status": "paused",
            "job_id": job_id,
            "reason": reason,
            "message": message,
            "success_count": success_count,
            "failed_count": failed_count,
            "done": len(job["done_ids"]),
            "pending_remaining": len(remaining),
            "items": items,
        }

    job["status"] = "completed"
    return {
        "status": "completed",
        "job_id": job_id,
        "success_count": success_count,
        "failed_count": failed_count,
        "items": items,
    }


# --- Collect + Doubao Extract + Write to Knowledge Base (一键全流程) ---


@router.post("/api/collect-and-extract/{platform}")
async def collect_and_extract(platform: str, request: Request, db: Session = Depends(get_db)):
    """Full pipeline: CDP collect favorites → Doubao extract content → write RawSource."""
    from app.connectors import bilibili_collector, xiaohongshu_collector
    from app.connectors.cdp_proxy import CDPConnectionError as _CDPErr
    from app.connectors.doubao_extractor import DoubaoExtractor

    data = await request_data(request)
    raw_limit = str(data.get("limit") or "10").strip()
    limit = None if raw_limit == "all" else int(raw_limit)
    homepage_url = str(data.get("homepage_url") or data.get("url") or "").strip()
    next_url = str(data.get("next") or f"/ui/source-setup/{platform}")

    # Step 1: Collect favorites via CDP
    effective_limit = limit or 1000
    try:
        if platform == "douyin":
            # Try CDP proxy first, fall back to Playwright collector
            try:
                from app.connectors.cdp_proxy import cdp_proxy as _proxy
                await _proxy.connect()
                tab = await _proxy.new_tab("https://www.douyin.com/user/self?showTab=favorite_collection")
                await _proxy.wait_for_load(tab)
                for _ in range(min(effective_limit // 3, 20)):
                    await _proxy.scroll(tab)
                import json as _json
                from pathlib import Path as _Path
                eval_path = _Path(__file__).resolve().parents[1] / "extension" / "douyin_eval.js"
                script = eval_path.read_text(encoding="utf-8") if eval_path.exists() else "(() => JSON.stringify([]))()"
                raw = await _proxy.eval_script(tab, script)
                raw_items = _json.loads(raw) if isinstance(raw, str) else (raw or [])
                await _proxy.close_tab(tab)
                items = [
                    ConnectorItem(raw_url=i.get("url", ""), title=i.get("title", ""), platform="douyin", content_type=i.get("kind", "video"), metadata={"source": "douyin_cdp_proxy"})
                    for i in raw_items[:effective_limit] if i.get("url")
                ]
            except _CDPErr:
                items = await douyin_browser_collector.extract_visible_video_links(limit=effective_limit, require_collection_page=False)
        elif platform == "bilibili":
            favorites_url = resolve_platform_favorites_url("bilibili", homepage_url)
            items = await bilibili_collector.extract_favorites(url=favorites_url, limit=effective_limit)
        elif platform == "xiaohongshu":
            favorites_url = resolve_platform_favorites_url("xiaohongshu", homepage_url)
            items = await xiaohongshu_collector.extract_favorites(url=favorites_url, limit=effective_limit)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    except _CDPErr as exc:
        if wants_html(request):
            return RedirectResponse(f"{next_url}?saved=collect-failed", status_code=303)
        raise HTTPException(status_code=503, detail=f"CDP Proxy 未连接: {exc}")
    except (BrowserDependencyMissing, DouyinPageNotReady) as exc:
        if wants_html(request):
            return RedirectResponse(f"{next_url}?saved=page-not-ready", status_code=303)
        raise HTTPException(status_code=409, detail=str(exc))

    if not items:
        if wants_html(request):
            return RedirectResponse(f"{next_url}?saved=page-not-ready", status_code=303)
        raise HTTPException(status_code=422, detail="未提取到任何收藏内容")

    # Step 2: Doubao extract content for each item
    extractor = DoubaoExtractor()
    enriched_items = []
    extract_count = 0
    try:
        for item in items:
            try:
                result = await extractor.extract_content(item.raw_url)
            except _CDPErr:
                # Doubao CDP failed — keep item without transcript
                enriched_items.append(ConnectorItem(
                    raw_url=item.raw_url,
                    title=item.title,
                    platform=item.platform,
                    author=item.author,
                    content_type=item.content_type,
                    metadata={**(item.metadata or {}), "doubao_extracted": False, "extract_error": "cdp_connection_failed"},
                ))
                continue
            if result.success and result.transcript:
                enriched_items.append(ConnectorItem(
                    raw_url=item.raw_url,
                    title=result.title or item.title,
                    platform=item.platform,
                    author=item.author,
                    content_type=item.content_type,
                    metadata={
                        **(item.metadata or {}),
                        "transcript": result.transcript,
                        "text_content": result.text_content,
                        "doubao_extracted": True,
                    },
                ))
                extract_count += 1
            else:
                # Keep item even if doubao fails (with metadata_only quality)
                enriched_items.append(ConnectorItem(
                    raw_url=item.raw_url,
                    title=item.title,
                    platform=item.platform,
                    author=item.author,
                    content_type=item.content_type,
                    metadata={**(item.metadata or {}), "doubao_extracted": False, "extract_error": result.error or "empty"},
                ))
    finally:
        await extractor.close()

    # Step 3: Import to SyncService + ingest as RawSource
    connector = ensure_connector(db, platform, f"{platform} 收藏夹", f"browser_{platform}")
    scan_result = await SyncService(db).import_items(connector, enriched_items, f"{platform}_collect_extract")
    candidate_ids = scan_result.candidate_ids or candidate_ids_for_items(db, enriched_items)

    raw_service = RawSourceService(db)
    ingested_count = 0
    for cid in candidate_ids:
        try:
            raw_service.ingest_candidate(cid)
            ingested_count += 1
        except Exception:
            continue

    track_event(db, "collect_and_extract_completed", {
        "platform": platform,
        "collected": len(items),
        "doubao_extracted": extract_count,
        "ingested": ingested_count,
    })

    if wants_html(request):
        return RedirectResponse(f"{next_url}?saved=collected-{len(items)}-{ingested_count}", status_code=303)
    return {
        "status": "completed",
        "platform": platform,
        "collected": len(items),
        "doubao_extracted": extract_count,
        "ingested": ingested_count,
        "candidate_ids": candidate_ids,
    }


# --- Wiki batch delete ---


@router.post("/api/wiki/batch-delete")
async def batch_delete_wiki_pages(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        page_ids = data.get("page_ids") or []
    else:
        form = await request.form()
        page_ids = form.getlist("page_ids")
    if isinstance(page_ids, str):
        page_ids = [page_ids]
    if not page_ids:
        raise HTTPException(status_code=400, detail="page_ids required")

    deleted = 0
    for pid in page_ids:
        page = db.query(WikiPage).filter(WikiPage.page_id == str(pid)).first()
        if page:
            db.delete(page)
            deleted += 1
    db.commit()

    if wants_html(request):
        return RedirectResponse(f"/ui/wiki?deleted={deleted}", status_code=303)
    return {"status": "deleted", "count": deleted}


# --- History clear ---


@router.post("/api/history/clear")
async def clear_history(request: Request, db: Session = Depends(get_db)):
    """Clear all candidates and scan logs (history)."""
    from app.models import ProductEvent
    candidate_count = db.query(CandidateItem).count()
    db.query(ScanLog).delete()
    db.query(KnowledgeClassification).delete()
    db.query(SyncLedgerItem).delete()
    db.query(CandidateItem).delete()
    db.query(ProductEvent).delete()
    db.commit()
    if wants_html(request):
        return RedirectResponse(f"/ui/history?cleared={candidate_count}", status_code=303)
    return {"status": "cleared", "count": candidate_count}


# --- Lint Agent ---


@router.post("/api/lint/run")
async def lint_run(db: Session = Depends(get_db)):
    from app.agent.lint_agent import LintAgent
    report = await LintAgent(db).run_full_check()
    return report


@router.get("/api/lint/report")
async def lint_report(db: Session = Depends(get_db)):
    from app.agent.lint_agent import LintAgent
    report = await LintAgent(db).run_full_check()
    return report


# --- Auto Sync Toggle ---


@router.post("/api/sync/toggle-auto")
async def toggle_auto_sync(request: Request, db: Session = Depends(get_db)):
    data = await request_data(request)
    connector_id = int(data.get("connector_id") or 0)
    enabled = truthy(data.get("enabled"), True)
    connector = db.get(Connector, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    connector.auto_sync_enabled = enabled
    db.commit()
    return {"connector_id": connector_id, "auto_sync_enabled": enabled}


# ─── Auto-distill API ───────────────────────────────────────────────────────


@router.post("/api/distill/trigger")
async def trigger_distill(db: Session = Depends(get_db)):
    """Return count of pending items for progress UI."""
    from app.services.auto_distill_service import AutoDistillService
    status = AutoDistillService(db).get_distill_status()
    return {"pending": status["pending"]}


@router.post("/api/distill/step")
async def distill_step(db: Session = Depends(get_db)):
    """Distill ONE pending item. Called repeatedly by frontend for progress."""
    from app.models import UserPreference, WikiCategory
    from app.services.auto_distill_service import AutoDistillService
    svc = AutoDistillService(db)
    existing_cats = {c.name for c in db.query(WikiCategory).all()}
    pages = await svc.distill_pending(limit=1)
    if not pages:
        return {"done": True, "page": None, "new_category": None}
    p = pages[0]
    cat = db.get(WikiCategory, p.category_id) if p.category_id else None
    cat_name = cat.name if cat else "未分类"
    is_new = cat_name not in existing_cats
    remaining = svc.get_distill_status()["pending"]
    return {
        "done": False,
        "page": {"id": p.id, "title": p.title, "category": cat_name},
        "new_category": cat_name if is_new else None,
        "remaining": remaining,
    }


@router.get("/api/distill/status")
def distill_status(db: Session = Depends(get_db)):
    from app.services.auto_distill_service import AutoDistillService
    return AutoDistillService(db).get_distill_status()


# ─── Wiki categories API ────────────────────────────────────────────────────


@router.get("/api/wiki/categories")
def wiki_categories_api(db: Session = Depends(get_db)):
    from app.models import WikiCategory, WikiPage
    from sqlalchemy import func
    cats = (
        db.query(WikiCategory, func.count(WikiPage.id))
        .outerjoin(WikiPage, WikiPage.category_id == WikiCategory.id)
        .group_by(WikiCategory.id)
        .order_by(WikiCategory.display_order)
        .all()
    )
    return [{"id": c.id, "name": c.name, "slug": c.slug, "count": cnt} for c, cnt in cats]


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
    """Return push items awaiting Like/Unlike feedback (every 5th push without feedback yet)."""
    from app.models import PushHistory
    # Find pushes where total_push_count was a multiple of 5 and no feedback given
    pending = (
        db.query(PushHistory)
        .filter(PushHistory.feedback == None)  # noqa: E711
        .order_by(PushHistory.pushed_at.desc())
        .limit(20)
        .all()
    )
    # Only show ones at 5th intervals: check if their sequence position % 5 == 0
    # Simpler: just show the most recent one without feedback if total_push_count % 5 == 0
    from app.models import PushSettings
    settings = db.query(PushSettings).first()
    if not settings or settings.total_push_count % 5 != 0:
        return []
    # Get the latest push without feedback
    latest = db.query(PushHistory).filter(PushHistory.feedback == None).order_by(PushHistory.pushed_at.desc()).first()  # noqa: E711
    if not latest:
        return []
    from app.models import WikiPage
    page = db.get(WikiPage, latest.wiki_page_id) if latest.wiki_page_id else None
    return [{
        "push_id": latest.id,
        "title": page.title if page else "",
        "category": latest.category_name or "",
    }]


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
