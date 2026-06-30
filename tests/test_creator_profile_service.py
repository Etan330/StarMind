"""
Tests for Creator Input Normalization Service.

Tests cover:
1. Douyin share text URL extraction
2. Xiaohongshu profile URL extraction
3. Account ID/name marked as lookup_required
4. Ambiguous search results handling
"""

from app.services.creator_profile_service import normalize_creator_input, CreatorInputResult


class TestDouyinShareTextExtraction:
    """Test cases for extracting Douyin URLs from share text."""

    def test_extract_douyin_short_url_from_share_text(self):
        """抖音分享文本中提取 v.douyin.com 短链"""
        share_text = "【抖音短视频】 看这里！ https://v.douyin.com/abc123/ 复制整段话，打开抖音搜索，直接观看视频！"
        result = normalize_creator_input("douyin", share_text)

        assert result.platform == "douyin"
        assert result.input_type == "direct_link"
        assert result.profile_url == "https://v.douyin.com/abc123/"

    def test_extract_douyin_url_from_plain_share_text(self):
        """从纯分享文本提取抖音链接"""
        share_text = "https://v.douyin.com/xyz789/"
        result = normalize_creator_input("douyin", share_text)

        assert result.platform == "douyin"
        assert result.input_type == "direct_link"
        assert "v.douyin.com" in result.profile_url

    def test_douyin_direct_profile_url(self):
        """抖音主页链接直接识别"""
        url = "https://www.douyin.com/user/MS4wLjABAAAAxxx"
        result = normalize_creator_input("douyin", url)

        assert result.platform == "douyin"
        assert result.input_type == "direct_link"
        assert result.profile_url == url

    def test_douyin_short_url_already_normalized(self):
        """已经是标准格式的抖音 URL"""
        url = "https://www.douyin.com/user/MS4wLjABAAAAxxx"
        result = normalize_creator_input("douyin", url)

        assert result.input_type == "direct_link"
        assert "douyin.com" in result.profile_url


class TestXiaohongshuProfileUrlExtraction:
    """Test cases for extracting Xiaohongshu profile URLs."""

    def test_extract_xiaohongshu_profile_url(self):
        """小红书主页链接正确识别和解析"""
        profile_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33"
        result = normalize_creator_input("xiaohongshu", profile_url)

        assert result.platform == "xiaohongshu"
        assert result.input_type == "direct_link"
        assert result.profile_url == profile_url
        assert "user/profile" in result.profile_url

    def test_xiaohongshu_profile_url_with_extra_params(self):
        """带额外参数的小红书主页 URL"""
        profile_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?xsec_token=ABC&xsec_source=pc_share"
        result = normalize_creator_input("xiaohongshu", profile_url)

        assert result.platform == "xiaohongshu"
        assert result.input_type == "direct_link"
        assert "5fb234c4000000000101db33" in result.profile_url

    def test_xiaohongshu_share_text_extraction(self):
        """小红书分享文本提取主页 URL"""
        share_text = "在小红书分享生活～ https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33"
        result = normalize_creator_input("xiaohongshu", share_text)

        assert result.platform == "xiaohongshu"
        assert result.input_type == "direct_link"
        assert "xiaohongshu.com/user/profile" in result.profile_url


class TestAccountIdMarkedAsLookupRequired:
    """Test cases for marking non-URL inputs as lookup_required."""

    def test_pure_account_id_marked_lookup_required(self):
        """纯数字账号 ID 标记为 lookup_required"""
        account_id = "1234567890"
        result = normalize_creator_input("douyin", account_id)

        assert result.input_type == "lookup_required"
        assert result.message is not None

    def test_account_id_with_at_prefix_marked_lookup_required(self):
        """带 @ 前缀的账号名标记为 lookup_required"""
        account_name = "@username"
        result = normalize_creator_input("douyin", account_name)

        assert result.input_type == "lookup_required"
        assert result.message is not None

    def test_plain_nickname_marked_lookup_required(self):
        """纯昵称（无 URL）标记为 lookup_required"""
        nickname = "李厂长来了"
        result = normalize_creator_input("douyin", nickname)

        assert result.input_type == "lookup_required"
        assert result.message is not None

    def test_xiaohongshu_nickname_lookup_required(self):
        """小红书昵称也标记为 lookup_required"""
        nickname = "小明同学"
        result = normalize_creator_input("xiaohongshu", nickname)

        assert result.input_type == "lookup_required"
        assert result.message is not None

    def test_ambiguous_short_text_marked_lookup_required(self):
        """短文本（可能是账号）标记为 lookup_required"""
        short_text = "test"
        result = normalize_creator_input("douyin", short_text)

        assert result.input_type == "lookup_required"


class TestResolveProfileAmbiguous:
    """Test cases for ambiguous search results."""

    def test_lookup_id_returns_ambiguous_when_multiple_matches(self):
        """账号 ID 搜索结果不唯一时返回 ambiguous"""
        # 当使用账号 ID 搜索，返回多个结果时应该返回 ambiguous
        # 模拟场景：账号 ID 可能匹配多个用户
        ambiguous_input = "1234567890"
        result = normalize_creator_input("douyin", ambiguous_input, search_results_count=3)

        assert result.input_type == "ambiguous"
        assert result.message is not None
        assert "主页" in result.message or "主页" in result.message

    def test_nickname_returns_ambiguous_when_multiple_matches(self):
        """昵称搜索结果不唯一时返回 ambiguous"""
        nickname = "李厂长来了"
        result = normalize_creator_input("douyin", nickname, search_results_count=2)

        assert result.input_type == "ambiguous"
        assert "主页" in result.message

    def test_lookup_returns_profile_url_when_single_match(self):
        """账号 ID/昵称搜索结果唯一时返回主页链接"""
        account_id = "1234567890"
        result = normalize_creator_input("douyin", account_id, search_results_count=1, resolved_profile_url="https://www.douyin.com/user/MS4wLjABAAAAxxx")

        assert result.input_type == "direct_link"
        assert result.profile_url == "https://www.douyin.com/user/MS4wLjABAAAAxxx"

    def test_ambiguous_requires_profile_url(self):
        """ambiguous 状态下应提示用户补主页链接"""
        ambiguous_input = "common_name"
        result = normalize_creator_input("douyin", ambiguous_input, search_results_count=5)

        assert result.input_type == "ambiguous"
        assert result.message is not None
        # 提示信息应该包含补主页的要求
        assert "主页" in result.message or "profile" in result.message.lower()


class TestCreatorInputNormalizationIntegration:
    """Integration tests for the full normalization flow."""

    def test_full_flow_douyin_share_to_profile(self):
        """完整流程：分享文本 -> 提取 URL -> 归一化"""
        share_text = "【精彩视频】 https://v.douyin.com/abc123/ 复制这条链接，打开抖音搜索观看"
        result = normalize_creator_input("douyin", share_text)

        assert result.platform == "douyin"
        assert result.input_type == "direct_link"
        assert result.profile_url is not None

    def test_full_flow_xiaohongshu_profile(self):
        """完整流程：小红书主页链接归一化"""
        profile_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33"
        result = normalize_creator_input("xiaohongshu", profile_url)

        assert result.platform == "xiaohongshu"
        assert result.input_type == "direct_link"
        assert result.profile_url == profile_url

    def test_unknown_platform_falls_back_to_url_detection(self):
        """未知平台时降级到 URL 检测"""
        # 如果平台未知但包含 URL，仍应尝试提取
        url = "https://www.douyin.com/user/test"
        result = normalize_creator_input("unknown", url)

        assert result.platform == "douyin"  # 应该推断出正确平台
        assert result.input_type == "direct_link"

    def test_empty_input_handling(self):
        """空输入处理"""
        result = normalize_creator_input("douyin", "")

        assert result.input_type == "lookup_required"
        assert result.message is not None
