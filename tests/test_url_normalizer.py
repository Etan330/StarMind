from app.api.routes import douyin_profile_base_url, douyin_profile_vid_fallback
from app.services.url_normalizer import normalize_url


def test_youtube_normalization_removes_tracking_and_extracts_video_id():
    normalized = normalize_url("http://www.youtube.com/watch?v=abc123&utm_source=x&timestamp=99")

    assert normalized.platform == "youtube"
    assert normalized.external_item_id == "abc123"
    assert normalized.canonical_url == "https://www.youtube.com/watch?v=abc123"


def test_bilibili_normalization_uses_bv_id():
    normalized = normalize_url("https://www.bilibili.com/video/BV1SM4y1K7ax?spm_id_from=333.999")

    assert normalized.platform == "bilibili"
    assert normalized.external_item_id == "BV1SM4y1K7ax"
    assert normalized.canonical_url == "https://www.bilibili.com/video/BV1SM4y1K7ax"


def test_github_normalization_uses_owner_repo():
    normalized = normalize_url("https://github.com/openai/openai-cookbook?utm_medium=social")

    assert normalized.platform == "github"
    assert normalized.external_item_id == "openai/openai-cookbook"
    assert normalized.canonical_url == "https://github.com/openai/openai-cookbook"


def test_douyin_normalization_extracts_video_id():
    normalized = normalize_url("https://www.douyin.com/video/7380000112233?utm_source=share")

    assert normalized.platform == "douyin"
    assert normalized.external_item_id == "7380000112233"
    assert normalized.canonical_url == "https://www.douyin.com/video/7380000112233"


def test_douyin_jingxuan_modal_id_normalizes_to_video_url():
    normalized = normalize_url("https://www.douyin.com/jingxuan?modal_id=7648123596673550565")

    assert normalized.platform == "douyin"
    assert normalized.external_item_id == "7648123596673550565"
    assert normalized.canonical_url == "https://www.douyin.com/video/7648123596673550565"


def test_douyin_profile_helpers_strip_modal_query_and_build_vid_fallback():
    profile_url = "https://www.douyin.com/user/abc?from_tab_name=main&vid=7648123596673550565"

    assert douyin_profile_base_url(profile_url) == "https://www.douyin.com/user/abc"
    fallback = douyin_profile_vid_fallback(profile_url, "李厂长来了")
    assert fallback is not None
    assert fallback.raw_url == "https://www.douyin.com/video/7648123596673550565"
    assert fallback.author == "李厂长来了"
