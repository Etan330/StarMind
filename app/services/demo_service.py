from __future__ import annotations

from typing import Any


DEMO_RESULTS: dict[str, dict[str, Any]] = {
    "second-brain": {
        "demo_id": "second-brain",
        "demo_type": "article",
        "quality_level": "ready",
        "title": "构建第二大脑：完整指南",
        "source_url": "https://fortelabs.com/blog/second-brain-guide",
        "platform": "article",
        "raw": {
            "title": "Building a Second Brain: The Complete Guide",
            "status": "RAW 已保存",
            "transcript_status": "provided",
            "saved_at": "2025/05/24 14:32",
            "size": "1.2 MB",
        },
        "wiki": {
            "title": "构建第二大脑：完整指南",
            "status": "待审核",
            "summary": "本文系统介绍了第二大脑的概念、核心原则与搭建方法，帮助知识工作者捕获、组织、关联和复用信息。",
            "bullets": [
                "第二大脑的核心是信任你的系统，而不是记住所有信息。",
                "信息需要经过组织、提炼与关联，才能真正支持行动。",
                "定期回顾与可执行输出是知识资产产生复利的关键。",
            ],
            "source_refs_count": 3,
        },
        "query": {
            "question": "第二大脑的核心原则是什么？",
            "answer": "核心原则包括：捕获一切、持续整理、渐进输出、关联思考，以及通过定期回顾让知识支持行动。",
            "sources": ["fortelabs.com/blog/second-brain-guide", "tiago-forte.com/para", "ai/blog/second-brain"],
            "has_target_source": True,
        },
        "warning": "",
    },
    "video-asr-pending": {
        "demo_id": "video-asr-pending",
        "demo_type": "video_pending",
        "quality_level": "asr_pending",
        "title": "视频示例：ASR 待补全",
        "source_url": "https://youtube.com/watch?v=example",
        "platform": "video",
        "raw": {
            "title": "Tiago Forte 谈 PARA 方法的核心实践",
            "status": "RAW 已保存",
            "transcript_status": "audio_asr_pending",
            "saved_at": "2025/05/24 11:08",
            "size": "metadata only",
        },
        "wiki": {
            "title": "PARA 方法实践要点",
            "status": "待补全文本",
            "summary": "当前只保存了标题、链接和少量页面信息。正式沉淀前需要补齐字幕或正文。",
            "bullets": [
                "视频类资料在没有逐字稿时不能直接当成高质量知识。",
                "StarMind 会保留来源，但把质量标记为需要补全文本。",
                "你可以稍后补全 ASR 后再重新生成草稿。",
            ],
            "source_refs_count": 1,
        },
        "query": {
            "question": "这个视频能直接沉淀为知识页吗？",
            "answer": "不能直接标记为高质量知识页。当前只有元数据，需要补全音频转写后再生成更可靠的草稿。",
            "sources": ["youtube.com/watch?v=example"],
            "has_target_source": True,
        },
        "warning": "这是 ASR 待补全示例，用于展示质量边界。",
    },
}


def get_demo_result(demo_id: str = "second-brain") -> dict[str, Any] | None:
    return DEMO_RESULTS.get(demo_id)


def list_demo_results() -> list[dict[str, Any]]:
    return list(DEMO_RESULTS.values())


def get_v3_home_preview() -> dict[str, Any]:
    demo = DEMO_RESULTS["second-brain"]
    return {
        "demo_id": demo["demo_id"],
        "input_label": "示例输入",
        "input_title": "Why Smart People Worry More",
        "input_url": "example.com/article/why-smart-people-worry-more",
        "summary": "优秀的人更容易过度思考与自我批评，但也更擅长把焦虑转化为行动线索。",
        "key_points": [
            "完美主义会放大焦虑感。",
            "认知能力越强，越容易预见风险。",
            "关注圈与行动圈可以降低信息负担。",
        ],
        "source_evidence": [
            "作者：Jane Example",
            "发布：2024-08-12",
            "匹配度：92%",
        ],
        "questions": [
            "如何区分高标准和完美主义？",
            "有哪些焦虑缓解练习可以立刻开始？",
            "如何建立可持续的复盘系统？",
        ],
        "trust_line": "示例不会写入你的知识库，真实结果需要你确认后才会保存。",
    }

