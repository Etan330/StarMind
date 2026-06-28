"""批量提取节奏节流 + 人机验证检测的无状态共享工具。

豆包与点点两个 extractor 同构但不强行抽基类（仅 2 个，避免过度工程）。
这里集中放三类跨提取器共用、且需要保证一致的东西：

1. ``pace_sleep`` —— 条间随机延时（单测可注入 ``sleep`` / ``rng`` 做确定性断言）。
2. ``next_pending`` —— 按平台读对应的 extracted 标记，给出尚未提取的 candidate id 列表。
3. ``CHALLENGE_DETECT_JS`` —— 人机验证检测的 JS 片段（字符串常量），
   两个 extractor 的 ``_challenge_state`` 共用同一段，保证检测口径一致。

放在 connectors 层而非 services，是因为它直接服务于 extractor 的页面自动化，
与 cdp_proxy / doubao_extractor 同源；端点只是调用方。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, Iterable, Mapping


# 各平台在 candidate.metadata_json 里的「已提取」持久标记键。
# 续跑时据此跳过已完成条目（与 routes.py already_extracted 语义一致）。
PLATFORM_EXTRACTED_FLAG = {
    "doubao": "doubao_extracted",
    "xiaohongshu_diandian": "xiaohongshu_diandian_extracted",
}


async def pace_sleep(
    delay_min: float,
    delay_max: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[float, float], float] = random.uniform,
) -> float:
    """随机停顿 [delay_min, delay_max] 秒，返回实际停顿秒数。

    ``sleep`` / ``rng`` 可注入：单测里 monkeypatch 成同步桩即可在不真正等待的
    情况下断言「停顿次数 = 条数-1」「停顿值落在区间内」。
    """
    lo = max(0.0, float(delay_min))
    hi = max(lo, float(delay_max))
    seconds = hi if hi == lo else rng(lo, hi)
    if seconds > 0:
        await sleep(seconds)
    return seconds


def next_pending(
    candidates_meta: Iterable[tuple[Any, Mapping[str, Any] | None]],
    platform: str,
) -> list[Any]:
    """从 (candidate_id, metadata_dict) 序列里筛出该平台尚未提取的 id。

    ``metadata_dict`` 为 None 视为未提取。标记真值用 truthy 判断，
    与 routes.py 里对 ``doubao_extracted`` 的读取保持一致。
    """
    flag = PLATFORM_EXTRACTED_FLAG.get(platform, f"{platform}_extracted")
    pending: list[Any] = []
    for candidate_id, metadata in candidates_meta:
        meta = metadata or {}
        if not bool(meta.get(flag)):
            pending.append(candidate_id)
    return pending


# 人机验证检测 JS：返回 {challenge_required, kind, message}。
# kind=login 时归类为登录弹窗（避免「验证码登录」被误判成人机验证），
# kind=human_verification 才是滑块/拼图/captcha 这类需要用户手动过的验证。
# 字节系常见特征：iframe[src*=captcha]、[class*=captcha]、[class*=secsdk]、滑块 verify。
CHALLENGE_DETECT_JS = r"""
(() => {
    const isVisible = (node) => {
        if (!node) return false;
        const rect = node.getBoundingClientRect?.();
        const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
        return (!rect || (rect.width > 0 && rect.height > 0)) && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
    };
    const bodyText = (document.body?.innerText || '').slice(0, 3000);
    // 登录关键词：若命中且同时像登录弹窗，则归 login，避免「验证码登录」误判为人机验证。
    const loginRe = /扫码登录|手机号登录|验证码登录|未登录|请登录|账号登录|sign in|log in/i;
    const challengeTextRe = /(滑动|拖动).*(验证|拼图)|滑块|拼图验证|安全验证|请完成验证|人机验证|captcha|verify|slide to verify/i;
    const hasLoginModal = Array.from(document.querySelectorAll('[role="dialog"], [class*="modal"], [class*="login" i], [class*="Login"]'))
        .some((node) => isVisible(node) && /登录|注册|扫码|手机号/.test(node.innerText || ''));

    const challengeNodes = [
        ...document.querySelectorAll('iframe[src*="captcha" i]'),
        ...document.querySelectorAll('[class*="captcha" i]'),
        ...document.querySelectorAll('[id*="captcha" i]'),
        ...document.querySelectorAll('[class*="slider" i][class*="verify" i]'),
        ...document.querySelectorAll('[class*="secsdk" i]'),
        ...document.querySelectorAll('[class*="vc-container" i], [class*="verify-container" i]'),
    ].filter(isVisible);

    const textChallenge = challengeTextRe.test(bodyText);
    const domChallenge = challengeNodes.length > 0;
    const challenge = textChallenge || domChallenge;

    // 登录优先：如果只是登录弹窗（没有滑块/captcha 这类 DOM），归 login。
    if (hasLoginModal && !domChallenge && !textChallenge) {
        return JSON.stringify({ challenge_required: false, kind: 'login', message: '检测到登录弹窗' });
    }
    if (challenge) {
        return JSON.stringify({
            challenge_required: true,
            kind: 'human_verification',
            message: '检测到人机验证（滑块/拼图/安全验证），需要在浏览器中手动完成',
        });
    }
    return JSON.stringify({ challenge_required: false, kind: 'none', message: '' });
})()
"""
