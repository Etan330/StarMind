from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agent import AgentRunner
from app.connectors import BrowserDependencyMissing, DouyinPageNotReady, douyin_browser_collector
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
    test_active_connection,
    test_model_connection,
)
from app.models import CandidateItem, Connector, KnowledgeClassification, RawSource, RecycleBinItem, ScanLog, SyncLedgerItem, WikiPage
from app.services import (
    ClassifierService,
    RawSourceService,
    RecycleService,
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
        "logo_url": "https://cdn.simpleicons.org/tiktok/FFFFFF",
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
                    (CandidateItem.platform == normalized.platform) & (CandidateItem.external_item_id == normalized.external_item_id),
                )
            )
            .first()
        )
        if candidate and candidate.id not in seen:
            metadata = json.loads(candidate.metadata_json or "{}")
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
    question = request.query_params.get("q")
    selected_profile_id = request.query_params.get("model_profile")
    selected_profile = get_model_profile(selected_profile_id)
    agent_response = None
    if question:
        agent_response = await AgentRunner(db).answer_question(
            question,
            provider_id=selected_profile.get("provider") if selected_profile else None,
            model=selected_profile.get("model") if selected_profile else None,
            model_profile_name=selected_profile.get("name") if selected_profile else None,
        )
        track_event(db, "query_submitted", {"page": "home", "has_sources": bool(agent_response.sources)})
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
            question=question,
            agent_response=agent_response,
            agent_response_html=render_markdown(agent_response.answer) if agent_response else "",
            created=request.query_params.get("created"),
            scan=request.query_params.get("scan"),
            connectors=connectors,
            settings=settings,
            model_profiles=profiles["profiles"],
            active_profile_id=get_active_profile_id(settings, profiles),
            selected_model_profile_id=selected_profile_id,
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
    return RedirectResponse("/ui/source-setup/douyin", status_code=303)


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
    api_key = str(data.get("api_key") or "").strip() or None
    result = save_model_settings(provider=provider, model=model, api_key=api_key)
    if wants_html(request):
        return RedirectResponse("/ui/settings?status=saved", status_code=303)
    return result


@router.post("/settings/model/test")
async def test_model_settings(request: Request):
    data = await request_data(request)
    provider = str(data.get("provider") or data.get("default_provider") or "").strip()
    model = str(data.get("model") or data.get("default_model") or "").strip()
    api_key = str(data.get("api_key") or "").strip() or None
    result = await test_model_connection(provider, model, api_key) if provider else await test_active_connection()
    if wants_html(request):
        state = "success" if result["ok"] else "failed"
        return RedirectResponse(f"/ui/settings?test={state}", status_code=303)
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
    provider_meta = get_providers().get(provider, {})
    default_name = f"{provider_meta.get('display_name', provider)} · {model}"
    name = str(data.get("name") or default_name).strip() or default_name
    use_case = str(data.get("use_case") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    activate = str(data.get("activate") or "on").lower() in {"1", "true", "on", "yes"}
    if api_key:
        save_provider_api_key(provider, api_key)
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
        save_model_settings(provider=provider, model=model, api_key=api_key or None)
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


@router.get("/ui/source-setup/{platform}", response_class=HTMLResponse)
def source_setup_page(platform: str, request: Request, db: Session = Depends(get_db)):
    preset = next((item for item in PLATFORM_PRESETS if item["platform"] == platform), None)
    if preset is None:
        raise HTTPException(status_code=404, detail="source platform not found")
    source_connections = get_source_connections()["connections"]
    connector = db.query(Connector).filter(Connector.platform == platform).first()
    return templates.TemplateResponse(
        request,
        "source_setup.html",
        template_context(
            request,
            "connectors",
            db,
            preset=preset,
            connector=connector,
            connection=source_connections.get(platform, {}),
            saved=request.query_params.get("saved"),
        ),
    )


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


@router.post("/xiaohongshu/browser/open")
async def open_xiaohongshu_browser(request: Request):
    data = await request_data(request)
    url = str(data.get("url") or "https://www.xiaohongshu.com/explore").strip()
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        if wants_html(request):
            return RedirectResponse("/ui/source-setup/xiaohongshu?saved=browser-missing", status_code=303)
        raise HTTPException(status_code=500, detail="Playwright 未安装，请先安装项目依赖。") from exc
    browser_dir = LOCAL_DATA_DIR / "browser" / "xiaohongshu"
    browser_dir.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            str(browser_dir),
            channel="chrome",
            headless=False,
            viewport={"width": 1360, "height": 900},
        )
    except Exception:
        context = await playwright.chromium.launch_persistent_context(
            str(browser_dir),
            headless=False,
            viewport={"width": 1360, "height": 900},
        )
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    BROWSER_SESSIONS["xiaohongshu"] = {"playwright": playwright, "context": context, "page": page}
    if wants_html(request):
        return RedirectResponse("/ui/source-setup/xiaohongshu?saved=browser-opened", status_code=303)
    return {"status": "opened", "current_url": page.url}


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
    url = str(data.get("url") or "https://www.douyin.com/user/self?showTab=favorite_collection").strip()
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
            items = await douyin_browser_collector.extract_visible_video_links(limit=limit, require_collection_page=False)
            if truthy(data.get("transcribe"), True):
                cookies_file = await douyin_browser_collector.export_cookies() if hasattr(douyin_browser_collector, "export_cookies") else None
                items, transcript_failures = enrich_douyin_items_with_report(
                    items,
                    DouyinTranscriptService(cookies_file=cookies_file),
                    limit=limit,
                    require_transcript=truthy(data.get("require_transcript"), True),
                )
                if not items:
                    fallback_item = douyin_profile_vid_fallback(profile_url, target_name)
                    if fallback_item:
                        fallback_items, fallback_failures = enrich_douyin_items_with_report(
                            [fallback_item],
                            DouyinTranscriptService(cookies_file=cookies_file),
                            limit=1,
                            require_transcript=truthy(data.get("require_transcript"), True),
                        )
                        transcript_failures.extend(fallback_failures)
                        items = fallback_items
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
    favorite_sources = [source for source in sources if source.source_type not in {"passive_link", "manual_idea", "distill_profile"}]
    link_items = db.query(CandidateItem).filter(CandidateItem.source_type == "passive_link").order_by(CandidateItem.created_at.desc()).all()
    idea_items = db.query(CandidateItem).filter(CandidateItem.source_type == "manual_idea").order_by(CandidateItem.created_at.desc()).all()
    source_id = request.query_params.get("source_id")
    selected_source = db.get(RawSource, int(source_id)) if source_id and source_id.isdigit() else (sources[0] if sources else None)
    selected_metadata = safe_json(selected_source.metadata_json) if selected_source else {}
    selected_transcript = read_local_text(selected_source.transcript_path if selected_source else None)
    selected_raw_text = read_local_text(selected_source.raw_content_path if selected_source else None)
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
    track_event(db, "page_viewed", {"page": "wiki"})
    section_id = request.query_params.get("section") or "knowledge"
    active_section = next((item for item in WIKI_SECTIONS if item["id"] == section_id), WIKI_SECTIONS[0])
    all_pages = db.query(WikiPage).order_by(WikiPage.last_updated_at.desc()).all()
    query = db.query(WikiPage).order_by(WikiPage.last_updated_at.desc())
    if active_section["id"] == "index":
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
