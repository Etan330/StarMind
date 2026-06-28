import asyncio

from app.connectors.extract_pacing import (
    CHALLENGE_DETECT_JS,
    PLATFORM_EXTRACTED_FLAG,
    next_pending,
    pace_sleep,
)


def test_next_pending_skips_already_extracted_per_platform():
    metas = [
        (1, {"doubao_extracted": True}),
        (2, {"doubao_extracted": False}),
        (3, {}),
        (4, None),
        (5, {"doubao_extracted": True, "other": 1}),
    ]
    assert next_pending(metas, "doubao") == [2, 3, 4]


def test_next_pending_uses_xiaohongshu_flag_independently():
    metas = [
        (1, {"xiaohongshu_diandian_extracted": True}),
        (2, {"doubao_extracted": True}),  # 豆包标记不该让点点跳过
        (3, {}),
    ]
    assert next_pending(metas, "xiaohongshu_diandian") == [2, 3]
    assert PLATFORM_EXTRACTED_FLAG["xiaohongshu_diandian"] == "xiaohongshu_diandian_extracted"


def test_pace_sleep_uses_injected_rng_and_sleep_and_returns_seconds():
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    seconds = asyncio.run(
        pace_sleep(15.0, 40.0, sleep=fake_sleep, rng=lambda lo, hi: (lo + hi) / 2)
    )

    assert seconds == 27.5
    assert sleeps == [27.5]


def test_pace_sleep_clamps_and_skips_zero_delay():
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    # delay_max < delay_min -> 收敛到 lo；lo==hi 时不调用 rng；0 秒不真正 sleep。
    seconds = asyncio.run(pace_sleep(0.0, -5.0, sleep=fake_sleep, rng=lambda lo, hi: 999))

    assert seconds == 0.0
    assert sleeps == []


def test_challenge_detect_js_contains_required_keywords():
    # plan E1：_challenge_state 脚本含 captcha/slider 关键词。
    assert "captcha" in CHALLENGE_DETECT_JS
    assert "slider" in CHALLENGE_DETECT_JS
    assert "secsdk" in CHALLENGE_DETECT_JS
    assert "challenge_required" in CHALLENGE_DETECT_JS
    assert "human_verification" in CHALLENGE_DETECT_JS
    # 登录优先归类，避免「验证码登录」误判为人机验证。
    assert "kind: 'login'" in CHALLENGE_DETECT_JS
