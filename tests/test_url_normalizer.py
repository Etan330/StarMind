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
