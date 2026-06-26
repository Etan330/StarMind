import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from app.connectors.bilibili import BilibiliFavoritesCollector
from app.connectors.xiaohongshu import XiaohongshuFavoritesCollector

ROOT = Path(__file__).resolve().parents[1]


JS_DOM_HARNESS = r'''
class Element {
  constructor(tag, attrs = {}, text = '', children = []) {
    this.tag = tag;
    this.attrs = attrs;
    this.innerText = text;
    this.children = children;
    this.parent = null;
    for (const child of children) child.parent = this;
  }
  getAttribute(name) {
    return this.attrs[name] || null;
  }
  matchesSelector(selector) {
    if (selector === 'a[href]') return this.tag === 'a' && !!this.attrs.href;
    if (selector === 'li') return this.tag === 'li';
    if (selector === 'section') return this.tag === 'section';
    if (selector.startsWith('.')) return (this.attrs.class || '').split(/\s+/).includes(selector.slice(1));
    const tagClassMatch = selector.match(/^([a-z]+)\[class\*="([^"]+)"\]$/i);
    if (tagClassMatch) return this.tag === tagClassMatch[1] && (this.attrs.class || '').includes(tagClassMatch[2]);
    const classMatch = selector.match(/^\[class\*="([^"]+)"\]$/);
    if (classMatch) return (this.attrs.class || '').includes(classMatch[1]);
    if (selector === '[title]') return !!this.attrs.title;
    return false;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const visit = (node) => {
      if (selectors.some((sel) => node.matchesSelector(sel))) result.push(node);
      for (const child of node.children) visit(child);
    };
    for (const child of this.children) visit(child);
    return result;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  closest(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    let node = this;
    while (node) {
      if (selectors.some((sel) => node.matchesSelector(sel))) return node;
      node = node.parent;
    }
    return null;
  }
}
const location = { href: 'https://example.test/' };
'''


def run_extractor(script_path: str, dom_js: str):
    script = (ROOT / script_path).read_text(encoding="utf-8")
    program = JS_DOM_HARNESS + "\n" + dom_js + "\nconst output = " + script + ";\nconsole.log(output);\n"
    result = subprocess.run(["node", "-e", program], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_bilibili_extractor_prefers_real_title_over_stats_for_same_bvid():
    items = run_extractor(
        "extension/bilibili_eval.js",
        r'''
const document = new Element('document', {}, '', [
  new Element('li', {class: 'fav-video-list'}, '', [
    new Element('a', {href: 'https://www.bilibili.com/video/BV1abcDEF123'}, '100.6万 3796 49:51'),
    new Element('a', {href: 'https://www.bilibili.com/video/BV1abcDEF123', class: 'fav-video-list__title'}, 'AI Agent 从入门到实战')
  ])
]);
''',
    )

    assert items[0]["title"] == "AI Agent 从入门到实战"


def test_bilibili_extractor_does_not_use_stats_as_title():
    items = run_extractor(
        "extension/bilibili_eval.js",
        r'''
const document = new Element('document', {}, '', [
  new Element('li', {class: 'fav-video-list'}, '', [
    new Element('a', {href: 'https://www.bilibili.com/video/BV1abcDEF123'}, '100.6万 3796 49:51')
  ])
]);
''',
    )

    assert items[0]["title"] == ""


def test_xiaohongshu_extractor_uses_container_title_when_cover_link_is_empty():
    items = run_extractor(
        "extension/xiaohongshu_eval.js",
        r'''
const document = new Element('document', {}, '', [
  new Element('section', {class: 'note-item'}, '', [
    new Element('a', {href: 'https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12'}, ''),
    new Element('div', {class: 'title'}, 'AI 工作流搭建经验')
  ])
]);
''',
    )

    assert items[0]["title"] == "AI 工作流搭建经验"


def test_xiaohongshu_extractor_does_not_use_url_as_title_when_title_missing():
    items = run_extractor(
        "extension/xiaohongshu_eval.js",
        r'''
const document = new Element('document', {}, '', [
  new Element('section', {class: 'note-item'}, '', [
    new Element('a', {href: 'https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12'}, '')
  ])
]);
''',
    )

    assert items == []


def test_xiaohongshu_extractor_builds_share_text_from_profile_collect_link():
    items = run_extractor(
        "extension/xiaohongshu_eval.js",
        r'''
const document = new Element('document', {}, '', [
  new Element('section', {class: 'note-item'}, '', [
    new Element('a', {href: 'https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33/6a338bc10000000021014bc8?xsec_token=AB2T5L_LjI-8h03NF7itAak6_gIB-MT1CkA7CyPjF_Jo0=&xsec_source=pc_collect'}, ''),
    new Element('div', {class: 'title'}, 'Anthropic博客的Agent Eval实践心得 - 孙沐晏')
  ])
]);
''',
    )

    assert items[0]["note_id"] == "6a338bc10000000021014bc8"
    assert items[0]["url"].endswith("xsec_source=pc_collect")
    assert items[0]["share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?")
    assert "source=webshare" in items[0]["share_url"]
    assert "xhsshare=pc_web" in items[0]["share_url"]
    assert "xsec_token=AB2T5L_LjI-8h03NF7itAak6_gIB-MT1CkA7CyPjF_Jo0%3D" in items[0]["share_url"]
    assert "xsec_source=pc_share" in items[0]["share_url"]
    assert "pc_collect" not in items[0]["share_url"]
    assert "Anthropic博客的Agent Eval实践心得" in items[0]["share_text"]
    assert items[0]["share_url"] in items[0]["share_text"]


def test_xiaohongshu_extractor_deduplicates_profile_and_discovery_links_for_same_note():
    items = run_extractor(
        "extension/xiaohongshu_eval.js",
        r'''
const document = new Element('document', {}, '', [
  new Element('section', {class: 'note-item'}, '', [
    new Element('a', {href: 'https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33/6a338bc10000000021014bc8?xsec_token=TOKEN&xsec_source=pc_collect'}, ''),
    new Element('a', {href: 'https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share'}, ''),
    new Element('div', {class: 'title'}, 'Anthropic博客的Agent Eval实践心得')
  ])
]);
''',
    )

    assert len(items) == 1
    assert items[0]["note_id"] == "6a338bc10000000021014bc8"
    assert items[0]["title"] == "Anthropic博客的Agent Eval实践心得"


def test_xiaohongshu_extractor_deduplicates_multiple_anchors_in_same_card():
    items = run_extractor(
        "extension/xiaohongshu_eval.js",
        r'''
const document = new Element('document', {}, '', [
  new Element('section', {class: 'note-item'}, '', [
    new Element('a', {href: 'https://www.xiaohongshu.com/explore/6a338bc10000000021014bc8'}, ''),
    new Element('a', {href: 'https://www.xiaohongshu.com/explore/6a338bc10000000021014bc8?xsec_source=pc_collect'}, 'Anthropic博客的Agent Eval实践心得'),
    new Element('div', {class: 'title'}, 'Anthropic博客的Agent Eval实践心得')
  ])
]);
''',
    )

    assert len(items) == 1
    assert items[0]["title"] == "Anthropic博客的Agent Eval实践心得"


def test_xiaohongshu_extractor_filters_short_ui_noise_titles():
    items = run_extractor(
        "extension/xiaohongshu_eval.js",
        r'''
const document = new Element('document', {}, '', [
  new Element('section', {class: 'note-item'}, '', [
    new Element('a', {href: 'https://www.xiaohongshu.com/explore/6a338bc10000000021014bc8'}, '[我'),
    new Element('div', {class: 'content'}, '[我'),
    new Element('div', {class: 'author'}, 'xiaohongshu')
  ]),
  new Element('nav', {class: 'global-nav'}, '', [
    new Element('a', {href: 'https://www.xiaohongshu.com/user/profile/abcdefabcdefabcdefabcdef'}, '我的')
  ])
]);
''',
    )

    assert items == []


class FakeProxy:
    def __init__(self, raw):
        self.raw = raw

    async def connect(self):
        return True

    async def new_tab(self, url):
        return SimpleNamespace(tab_id="tab-1", url=url)

    async def wait_for_load(self, tab):
        return None

    async def scroll(self, tab):
        return None

    async def eval_script(self, tab, script):
        return json.dumps(self.raw)

    async def close_tab(self, tab):
        return None


def test_bilibili_collector_marks_missing_title_without_using_url():
    url = "https://www.bilibili.com/video/BV1abcDEF123"
    collector = BilibiliFavoritesCollector(proxy=FakeProxy([{"url": url, "title": "", "bvid": "BV1abcDEF123"}]))

    items = asyncio.run(collector.extract_favorites("https://space.bilibili.com/1/favlist?fid=2", limit=1))

    assert items[0].title == "未识别标题"
    assert items[0].raw_url == url
    assert items[0].metadata["title_missing"] is True


def test_xiaohongshu_collector_marks_missing_title_without_using_url():
    url = "https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12"
    collector = XiaohongshuFavoritesCollector(proxy=FakeProxy([{"url": url, "title": ""}]))

    items = asyncio.run(collector.extract_favorites("https://www.xiaohongshu.com/user/profile/abc?tab=fav", limit=1))

    assert items[0].title == "未识别标题"
    assert items[0].raw_url == url
    assert items[0].metadata["title_missing"] is True


def test_xiaohongshu_collector_stores_share_metadata():
    url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33/6a338bc10000000021014bc8?xsec_source=pc_collect"
    share_url = "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share"
    share_text = "【Anthropic博客的Agent Eval实践心得 | 小红书 - 你的生活兴趣社区】 " + share_url
    collector = XiaohongshuFavoritesCollector(proxy=FakeProxy([
        {
            "url": url,
            "title": "Anthropic博客的Agent Eval实践心得",
            "note_id": "6a338bc10000000021014bc8",
            "xsec_token": "",
            "share_url": share_url,
            "share_text": share_text,
        }
    ]))

    items = asyncio.run(collector.extract_favorites("https://www.xiaohongshu.com/user/profile/abc?tab=fav", limit=1))

    assert items[0].raw_url == url
    assert items[0].metadata["xiaohongshu_note_id"] == "6a338bc10000000021014bc8"
    assert items[0].metadata["xiaohongshu_share_url"] == share_url
    assert items[0].metadata["xiaohongshu_share_text"] == share_text


def test_xiaohongshu_collector_deduplicates_notes_and_skips_noise():
    profile_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33/6a338bc10000000021014bc8?xsec_source=pc_collect"
    share_url = "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share"
    collector = XiaohongshuFavoritesCollector(proxy=FakeProxy([
        {"url": profile_url, "title": "Anthropic博客的Agent Eval实践心得", "note_id": "6a338bc10000000021014bc8", "share_url": share_url},
        {"url": share_url, "title": "Anthropic博客的Agent Eval实践心得", "note_id": "6a338bc10000000021014bc8", "share_url": share_url},
        {"url": "https://www.xiaohongshu.com/explore/6a338bc10000000021014bc9", "title": "[我", "note_id": "6a338bc10000000021014bc9"},
        {"url": "", "title": "不是收藏"},
    ]))

    items = asyncio.run(collector.extract_favorites("https://www.xiaohongshu.com/user/profile/abc?tab=fav", limit=10))

    assert len(items) == 1
    assert items[0].raw_url == profile_url
    assert items[0].title == "Anthropic博客的Agent Eval实践心得"
    assert items[0].metadata["xiaohongshu_note_id"] == "6a338bc10000000021014bc8"
