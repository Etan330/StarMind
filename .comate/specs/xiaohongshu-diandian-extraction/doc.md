# 小红书收藏夹改用点点逐条提取方案

## 背景与目标

当前收藏夹筛选链路是：在平台页扫描标题 → AI 批量分类 → 用户勾选要保留的条目 → `/api/sync/prepare-selected` 写入候选 → 前端统一调用 `/api/doubao/extract-selected`，由 `DoubaoExtractor` 打开豆包网页版，逐条发送「链接 + 通用 Prompt」，再把返回内容写入 RawSource。

本次要调整的是**仅针对小红书收藏夹**的深度提取阶段：用户在小红书收藏夹完成标题扫描和 AI 分类后，点击「仅提取我勾选的内容」时，不再把小红书条目发送到豆包，而是打开小红书站内 AI 点点固定入口 `https://www.xiaohongshu.com/ai_chat`，复用用户已经登录小红书的浏览器态，逐条发送「小红书分享文本/分享链接 + Prompt」，并抓取点点返回结果入库。

非小红书平台（抖音、B站等）保持现有豆包流程不变。

## 已分析的现有实现

- `/Users/wyy/ ai/starmind/StarMind-main/app/static/app.js`
  - `scanButton` 调用 `/api/sync/scan-titles` 扫描标题。
  - `classifyButton` 调用 `/api/classify/batch-titles` 做 AI 分类。
  - `extractButton` 当前固定调用 `/api/sync/prepare-selected` 后再调用 `/api/doubao/extract-selected`，状态文案也固定写为「豆包提取中」。
- `/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`
  - `scan_titles()` 根据 platform 调用小红书/B站/抖音采集器，返回 `{url,title,author,platform,content_type,metadata}`。
  - `prepare_selected_items()` 将勾选条目写成 CandidateItem，并将未勾选条目写入 SyncLedger 防止重复扫描。
  - `extract_selected_with_doubao()` 遍历 candidate_ids，调用 DoubaoExtractor，成功后把提取内容写入 candidate metadata，再调用 RawSourceService 入库。
- `/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu.py`
  - `XiaohongshuFavoritesCollector.extract_favorites()` 用 CDP 打开收藏页，注入 `extension/xiaohongshu_eval.js` 提取收藏条目。
  - 当前 metadata 只有 `source: xiaohongshu_cdp_favorites` 和缺标题标记。
- `/Users/wyy/ ai/starmind/StarMind-main/extension/xiaohongshu_eval.js`
  - 当前只返回 `url/title/author`，URL 是页面中的笔记地址，例如 `/explore/...` 或 `/discovery/item/...`，不是用户要求的「分享文本/分享链接」。
- `/Users/wyy/ ai/starmind/StarMind-main/app/connectors/doubao_extractor.py`
  - 已有可复用的 CDP 聊天页自动化模式：定位输入框、写入 prompt、点击发送、等待消息稳定、返回内容。
- `/Users/wyy/ ai/starmind/StarMind-main/tests/test_doubao_extract_selected.py`
  - 覆盖现有豆包提取、prepare-selected 复用候选、小红书候选场景等。
- `/Users/wyy/ ai/starmind/StarMind-main/tests/test_favorite_title_extractors.py`
  - 覆盖小红书标题扫描脚本和 Collector 缺标题行为。

## 需求场景与处理逻辑

### 场景 1：小红书收藏夹标题扫描保留可分享信息

用户扫描小红书收藏夹时，系统仍然读取收藏页标题用于 AI 分类。但为了后续发给点点，扫描结果需要尽量保存「分享格式」字段，目标格式类似：

```text
39 【Anthropic博客的Agent Eval实践心得 - 孙沐晏 | 小红书 - 你的生活兴趣社区】 😆 UALrEhx3GY3QXfM 😆 https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_token=...&xsec_source=pc_share
```

核心要求：

- 发给点点的不是收藏页/个人 profile 下的直接笔记地址，例如：
  - `https://www.xiaohongshu.com/user/profile/.../6a338bc10000000021014bc8?xsec_source=pc_collect`
- 发给点点的应是「小红书分享所显示的链接」，优先为：
  - 带标题的分享文本；
  - 其中 URL 形态为 `https://www.xiaohongshu.com/discovery/item/{note_id}?source=webshare&xhsshare=pc_web&xsec_token=...&xsec_source=pc_share`。
- 分享文本必须带标题；如果标题缺失，至少带现有扫描标题或 `未识别标题`，但后续测试应优先保证标题采集。

### 场景 2：AI 分类后仅小红书改走点点

用户在小红书平台的分类结果中勾选条目后点击「仅提取我勾选的内容」：

1. 前端仍调用 `/api/sync/prepare-selected`，确保候选创建、已选/跳过 ledger 记录逻辑不变。
2. 如果当前 platform 是 `xiaohongshu`，前端改调用新的点点提取接口，例如 `/api/xiaohongshu/diandian/extract-selected`。
3. 如果当前 platform 不是 `xiaohongshu`，继续调用 `/api/doubao/extract-selected`。
4. 页面文案根据平台切换：小红书显示「点点提取中」「正在发送到小红书点点」；其他平台仍显示豆包文案。

### 场景 3：点点逐条提取并写入 RawSource

新增小红书点点提取器后，接口遍历 candidate_ids：

1. 打开或复用 `https://www.xiaohongshu.com/ai_chat` 标签页。
2. 因用户已经登录小红书并能够抓取收藏夹，默认不做额外登录引导；但如果页面无输入框或出现明显登录态异常，应返回可读错误，如 `xiaohongshu_diandian_not_ready`。
3. 对每个 candidate 构造 prompt：优先使用 metadata 中的 `xiaohongshu_share_text`；如果没有，则用 title + 由 raw_url/canonical_url 转换出的 `discovery/item` 分享 URL。
4. 像豆包一样逐条发送、等待点点回复稳定、读取最后一条有效回复。
5. 成功后把回复内容写入 candidate metadata，并调用 RawSourceService 入库。
6. 失败条目不影响其他条目继续处理；失败时 ledger 标记为 `xiaohongshu_diandian_failed`。

## 技术方案

推荐方案：**新增 `XiaohongshuDiandianExtractor`，并在前端按 platform 分流**。

理由：

- 点点和豆包的网页结构、登录态、错误语义、Prompt 输入内容不同，不应把 DoubaoExtractor 改成多平台大杂烩。
- 后端保留独立接口，便于测试、错误码、metadata 字段与未来维护。
- 前端只做最小分流，现有 prepare-selected、分类、候选写入、RawSource 入库能力都复用。

### 新增/修改文件范围

#### 1. `/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu_diandian_extractor.py`（新增）

新增点点网页自动化提取器，建议结构参考 `DoubaoExtractor`：

```python
DIANDIAN_URL = "https://www.xiaohongshu.com/ai_chat"

XIAOHONGSHU_DIANDIAN_PROMPT = """请打开并解析下面这条小红书笔记分享内容，尽可能提取原始信息，不要只做摘要。

请重点提取：
1. 笔记标题
2. 正文内容
3. 图片中的文字、图表信息或截图文字
4. 作者明确表达的步骤、方法、经验、清单和结论
5. 如果无法访问或内容不可见，请明确说明原因

输出要求：
- 保留原文信息，尽量完整；
- 按标题、正文/OCR、要点、内容类型判断组织；
- 不要编造页面不可见内容。

小红书分享内容：
{share_text}
"""
```

核心方法：

- `_ensure_tab()`：连接 CDP，优先复用已打开的 `xiaohongshu.com/ai_chat` 标签页，否则新开固定 URL。
- `check_ready()`：检测页面可输入状态，不再要求用户登录豆包。
- `extract_content(share_text, url, content_type="note", timeout_seconds=240)`：发送 prompt 并返回 ExtractResult。
- `_send_prompt()`：复用豆包中输入框/发送按钮的稳健选择器思路，但错误码改为小红书语义。
- `_message_state()` / `_wait_for_response_complete()`：根据点点页面 DOM 调整 selector。初版可以使用通用 selector：`[class*="message"], [class*="markdown"], [class*="answer"], [data-testid*="message"], [class*="chat"]`，并通过测试 fake proxy 覆盖。
- `close()`：默认可关闭系统新开的点点 tab；登录/未就绪时保留 tab 便于用户查看。

返回结果可复用 dataclass 形态：

```python
@dataclass
class DiandianExtractResult:
    url: str
    transcript: str
    text_content: str
    title: str
    success: bool
    error: str | None = None
    prompt: str = ""
    elapsed_seconds: float | None = None
```

#### 2. `/Users/wyy/ ai/starmind/StarMind-main/extension/xiaohongshu_eval.js`（修改）

在现有 `url/title/author` 基础上新增分享字段：

- `note_id`：从 `/explore/{id}`、`/discovery/item/{id}` 或 profile 路径末尾提取。
- `xsec_token`：从原链接 query 中读取并保留。
- `share_url`：构造为 `https://www.xiaohongshu.com/discovery/item/{note_id}?source=webshare&xhsshare=pc_web`，如果有 `xsec_token`，追加 `xsec_token=...`，最后追加 `xsec_source=pc_share`。
- `share_text`：用标题组成接近小红书复制分享格式的文本，例如：

```javascript
const shareText = `${title ? `【${title} | 小红书 - 你的生活兴趣社区】 ` : ''}${shareUrl}`
```

如果页面 DOM 中能读取真实分享按钮/复制分享文本，可优先使用真实文本；否则使用上述稳定构造作为 fallback。

#### 3. `/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu.py`（修改）

将 eval 返回的 `share_url/share_text/note_id/xsec_token` 写入 ConnectorItem metadata：

```python
metadata = {
    "source": "xiaohongshu_cdp_favorites",
    "xiaohongshu_note_id": item.get("note_id"),
    "xiaohongshu_share_url": item.get("share_url"),
    "xiaohongshu_share_text": item.get("share_text"),
}
```

`raw_url` 仍保留原始可打开链接，确保去重和源网页打开逻辑不被破坏。后续点点提取使用 metadata 中的 share_text，而不是 raw_url。

#### 4. `/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`（修改）

新增辅助函数和接口：

```python
def xiaohongshu_share_text_for_candidate(candidate: CandidateItem) -> tuple[str, str]:
    metadata = safe_json(candidate.metadata_json)
    share_text = str(metadata.get("xiaohongshu_share_text") or "").strip()
    share_url = str(metadata.get("xiaohongshu_share_url") or "").strip()
    if share_text:
        return share_text, share_url or candidate.raw_url or candidate.canonical_url
    # fallback: 从 raw_url/canonical_url 构造 discovery/item 分享链接，并带标题
```

新增接口：

```text
POST /api/xiaohongshu/diandian/extract-selected
```

请求体沿用豆包接口：

```json
{
  "candidate_ids": [1, 2, 3],
  "per_item_timeout_seconds": 240,
  "generate_wiki_draft": false
}
```

接口逻辑与 `extract_selected_with_doubao()` 保持一致，但：

- 使用 `XiaohongshuDiandianExtractor`。
- 错误码为 `xiaohongshu_diandian_not_ready` / `xiaohongshu_diandian_failed`。
- metadata 字段使用：
  - `xiaohongshu_diandian_extracted: true`
  - `xiaohongshu_diandian_prompt`
  - `xiaohongshu_diandian_share_text`
  - `xiaohongshu_diandian_share_url`
  - `xiaohongshu_diandian_extracted_at`
  - `xiaohongshu_diandian_response_length`
  - `xiaohongshu_diandian_elapsed_seconds`
- 成功后 ledger `classification_label` 改为 `knowledge`，与豆包成功路径一致。

#### 5. `/Users/wyy/ ai/starmind/StarMind-main/app/static/app.js`（修改）

在点击「仅提取我勾选的内容」时按平台切换：

```javascript
const isXiaohongshu = platform === "xiaohongshu"
setBusy(extractButton, true, isXiaohongshu ? "点点提取中..." : "豆包提取中...")
setStatus(isXiaohongshu
  ? `已选择 ${selected.length} 条。正在创建候选并发送到小红书点点，单条可能等待数分钟。`
  : `已选择 ${selected.length} 条。正在创建候选并发送到豆包，单条可能等待数分钟。`)
const extractEndpoint = isXiaohongshu ? "/api/xiaohongshu/diandian/extract-selected" : "/api/doubao/extract-selected"
const extracted = await apiPost(extractEndpoint, { candidate_ids: selectedCandidateIds, per_item_timeout_seconds: 240 })
```

错误处理补充小红书点点错误码，提示用户确认浏览器仍登录小红书，并可打开 `https://www.xiaohongshu.com/ai_chat` 查看。

#### 6. `/Users/wyy/ ai/starmind/StarMind-main/app/templates/source_setup.html`（修改）

小红书平台的前置条件文案从「浏览器已登录豆包」改为「浏览器已登录小红书，点点入口可用」。B站/抖音仍保留豆包前置条件。

## 数据流路径

### 小红书新数据流

```text
source_setup.html
  → app.js 扫描标题
  → POST /api/sync/scan-titles platform=xiaohongshu
  → XiaohongshuFavoritesCollector
  → extension/xiaohongshu_eval.js 返回 url/title/author/share_url/share_text
  → app.js AI 分类
  → POST /api/classify/batch-titles
  → 用户勾选
  → POST /api/sync/prepare-selected
  → CandidateItem.metadata_json 保存 xiaohongshu_share_text/xiaohongshu_share_url
  → POST /api/xiaohongshu/diandian/extract-selected
  → XiaohongshuDiandianExtractor 打开 https://www.xiaohongshu.com/ai_chat
  → 逐条发送 share_text + prompt
  → 抓取点点回复
  → CandidateItem.metadata_json 写入提取内容
  → RawSourceService.ingest_candidate()
  → RawSource / local_data/raw_sources
```

### 非小红书数据流

保持现状：

```text
用户勾选
  → /api/sync/prepare-selected
  → /api/doubao/extract-selected
  → DoubaoExtractor
  → RawSourceService.ingest_candidate()
```

## 边界条件与异常处理

- CDP Proxy 不可用：沿用 503，提示 CDP Proxy 未连接。
- 点点页没有输入框：返回 428 或 503，错误码 `xiaohongshu_diandian_not_ready`，前端提示打开点点入口检查登录态/页面状态。
- 点点发送失败：单条标记失败，不终止后续条目；如果是全局页面不可用，接口可以直接返回 428。
- 点点返回空内容或超时：该条失败，ledger 标记 `xiaohongshu_diandian_failed`。
- 旧候选没有 `xiaohongshu_share_text`：后端用 candidate title + 从 note id 构造出的 `discovery/item` 分享 URL 兜底。
- note id 无法从 URL 提取：兜底使用 raw_url，但 prompt 中仍必须包含标题；同时在 metadata 中标记 `xiaohongshu_share_fallback: "raw_url"`。
- 已经生成 RawSource 或已提取过的候选：沿用 `candidate_ids_for_items()` 当前逻辑避免重复提取。需要同步识别 `xiaohongshu_diandian_extracted`，避免小红书成功后被重复准备。
- 不绕过登录、验证码、私有权限：只使用用户已登录浏览器中可见/可访问内容。

## 测试方案

新增和调整测试：

1. `tests/test_favorite_title_extractors.py`
   - 小红书 eval 脚本返回 `share_url`，且 host/path 为 `www.xiaohongshu.com/discovery/item/{id}`。
   - `share_url` 保留 `xsec_token`，并将 `xsec_source` 改为 `pc_share`。
   - `share_text` 包含标题，不使用 profile 直链作为发给点点的主要文本。
   - `XiaohongshuFavoritesCollector` 将 `xiaohongshu_share_text` 写入 metadata。
2. 新增或扩展 `tests/test_xiaohongshu_diandian_extract_selected.py`
   - fake extractor 成功返回内容时，接口创建 RawSource，并写入 `xiaohongshu_diandian_extracted` metadata。
   - 提取器收到的是分享文本，而不是 candidate.raw_url/profile 直链。
   - 旧候选没有分享文本时，后端 fallback 构造 `discovery/item` 分享 URL 并带标题。
   - 点点未就绪时返回明确错误码。
3. `tests/test_doubao_extract_selected.py`
   - 保持现有豆包测试通过，确保非小红书平台不受影响。

## 预期结果

- 小红书收藏夹完成 AI 分类后，「仅提取我勾选的内容」会走小红书点点，不再走豆包。
- 发给点点的内容优先是带标题的小红书分享文本/分享链接，而不是收藏页或 profile 下的直接笔记地址。
- 点点逐条返回的内容会像原豆包流程一样写入 RawSource，可在「原始资料」查看。
- 其他平台仍维持豆包提取流程。
