# 小红书收藏扫描去重与点点发送可靠性修复

## 背景与问题

用户在 `http://127.0.0.1:8000/ui/source-setup/xiaohongshu` 使用小红书收藏页流程时反馈两个问题：

1. 扫描标题后，同一条笔记重复出现。例如 `Anthropic博客的Agent Eval实践心得` 出现两次。
2. 扫描结果中出现异常条目，例如标题类似 `[我`，平台显示 `xiaohongshu`，该条目不是用户收藏。
3. 点点提取时，系统只把 prompt 和分享链接写入点点输入框，没有可靠点击发送箭头，也没有等待点点生成完成，因此没有成功写入 RawSource / 原始资料。

## 当前实现分析

### 小红书扫描路径

- 前端入口：`/Users/wyy/ ai/starmind/StarMind-main/app/static/app.js`
  - `data-filter-scan` 点击后调用 `/api/sync/scan-titles`。
- 后端入口：`/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`
  - `scan_titles()` 中 `platform == "xiaohongshu"` 时调用 `xiaohongshu_collector.extract_favorites()`。
- Collector：`/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu.py`
  - `XiaohongshuFavoritesCollector.extract_favorites()` 打开收藏页、滚动后注入 eval 脚本。
- DOM eval 脚本：`/Users/wyy/ ai/starmind/StarMind-main/extension/xiaohongshu_eval.js`
  - 当前遍历页面全部 `a[href]`。
  - `validUrl()` 只要能从 URL 路径中找到 12 位以上十六进制样式 note id，就接受。
  - `container = a.closest('[class*="note-item"], [class*="card"], section, li') || a`，候选容器范围较宽。
  - `candidateSelectors` 包含 `[class*="content"]`，可能抓到页面其他区域文本。
  - 去重 key 使用 `noteId`，理论上同一 note id 应去重；如果同一笔记在 DOM 中同时存在不同 URL 形态且提取到不同 note id，或异常链接被识别为 note id，就会漏掉去重。

### 点点提取路径

- 后端接口：`/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`
  - `/api/xiaohongshu/diandian/extract-selected` 对每个候选调用 `XiaohongshuDiandianExtractor.extract_content()`。
- 点点提取器：`/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu_diandian_extractor.py`
  - `_send_prompt()` 当前通过 JS 设置输入框内容，再在页面内筛选疑似发送按钮，先执行 `sendButton.click()`，再尝试 `self._proxy.click_at()`。
  - 但 `CDPProxy.click_at()` 当前调用的是 `POST /clickXY?target=...`，而 web-access CDP Proxy 文档里的真实鼠标点击接口是 `POST /clickAt?target=...`，这会导致真实点击兜底很可能没有生效。
  - `_send_prompt()` 只要页面内 `sendButton.click()` 返回 success 就认为发送成功，没有验证输入框是否清空、消息数量是否增加、或新用户消息是否进入对话。
  - `_wait_for_response_complete()` 根据 message 节点数量和最后文本稳定判断；如果消息节点选择器不匹配点点真实 DOM，可能一直拿不到新回复。

## 根因假设

### 问题 1：重复和异常扫描结果

可能根因：

1. `extension/xiaohongshu_eval.js` 扫描全页所有 `a[href]`，没有限制在收藏笔记卡片区域内。
2. `extractNoteId()` 对 profile 路径采用“从后往前找任意 12 位以上十六进制段”的策略，可能把非收藏笔记 URL 中的十六进制 ID 误认为 note id。
3. 仅在 eval 脚本内按 `noteId` 去重，但 Collector 侧没有再做防御式去重；如果 eval 输出重复，后端会原样返回。
4. 标题过滤过宽，`[我` 这类短且明显异常的 UI 文本没有被过滤。

### 问题 2：点点未发送、未等待结果

可能根因：

1. `CDPProxy.click_at()` 使用了错误 endpoint `/clickXY`，导致真实鼠标点击没有执行。
2. `_send_prompt()` 在页面内 `sendButton.click()` 后缺少发送结果验证。
3. 点点真实页面可能需要用户手势触发发送按钮，单纯 JS click 不稳定。
4. `_message_state()` 对点点消息节点选择器不够贴合，无法可靠区分“输入框里已有 prompt”与“prompt 已发送成用户消息 / 点点已回复”。

## 修复目标

1. 小红书扫描结果中同一 note id 只出现一次。
2. 过滤明显异常标题，例如 `[我`、过短孤立 UI 文本、没有有效标题且不是可确认笔记卡片的条目。
3. 保留用户要求的分享文本：发给点点的仍是 `【标题 | 小红书 - 你的生活兴趣社区】 discovery/item 分享链接`。
4. 点点提取必须可靠完成发送动作：
   - 设置输入框内容后点击正确的发送箭头。
   - 使用真实鼠标点击接口作为主要/兜底手段。
   - 发送后验证消息数量增加或输入框清空。
5. 点点提取必须等待点点回复稳定后再写入 RawSource。
6. 若发送失败，接口应返回明确错误，不能假装成功。

## 技术方案

### 1. 增强小红书 eval 脚本过滤与去重

修改文件：`/Users/wyy/ ai/starmind/StarMind-main/extension/xiaohongshu_eval.js`

计划调整：

- 收窄有效 URL 判断：
  - 明确支持：
    - `/explore/{noteId}`
    - `/discovery/item/{noteId}`
    - `/user/profile/{userId}/{noteId}`
  - 避免从任意路径段里盲目提取 note id。
- 收窄候选容器：优先使用小红书笔记卡片容器，如：
  - `[class*="note-item"]`
  - `[class*="NoteItem"]`
  - `[class*="feeds-page"] section`
  - `[class*="card"]` 仅作为 fallback
- 过滤异常标题：
  - 长度小于 2 的文本过滤。
  - 以 `[` 开头且长度很短的文本过滤，例如 `[我`。
  - 明显 UI 词、按钮文本、导航文本过滤。
- 输出前二次去重：按 `note_id` 去重，并优先保留标题分数高、标题非空、share_text 完整的项。

### 2. Collector 侧增加防御式去重

修改文件：`/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu.py`

计划调整：

- 在 `extract_favorites()` 中增加 `seen_note_keys`。
- key 优先级：
  - `note_id`
  - `xiaohongshu_share_url` 中的 note id
  - canonical/raw URL 去掉 query 后的路径
- 跳过重复 key。
- 对明显异常标题或缺少有效 note id 的条目进行跳过或标记为 title_missing；对小红书收藏页场景，优先跳过没有 note id 的条目。

### 3. 修正 CDP 真实点击接口

修改文件：`/Users/wyy/ ai/starmind/StarMind-main/app/connectors/cdp_proxy.py`

计划调整：

- 将 `click_at()` 的 endpoint 从 `/clickXY` 改为 `/clickAt`。
- 请求体按 web-access proxy 文档要求传 selector 或坐标；如果当前 proxy 实际支持 JSON 坐标，则保留兼容 fallback：
  - 先尝试 `/clickAt?target=...` + JSON 坐标。
  - 如失败，再尝试旧 `/clickXY?target=...`，避免破坏已有调用。

### 4. 强化点点发送验证

修改文件：`/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu_diandian_extractor.py`

计划调整：

- `_send_prompt()` 改为：
  1. 记录发送前消息数量。
  2. 填入 prompt。
  3. 定位发送按钮，返回按钮中心坐标。
  4. 优先调用 `self._proxy.click_at(tab, x, y)` 执行真实点击。
  5. 再用 Enter / JS click 作为 fallback。
  6. 轮询验证：
     - 输入框清空，或
     - 消息数量增加，或
     - 页面消息区域出现 prompt 前缀。
  7. 验证失败则返回 `xiaohongshu_diandian_send_not_confirmed`。
- `_wait_for_response_complete()` 增加：
  - 忽略最后一条仅等于用户 prompt 的消息。
  - 等到最后消息不是 prompt 且长度超过阈值。
  - 至少稳定 2 轮后返回，避免截断回复。

### 5. 测试覆盖

修改测试文件：

- `/Users/wyy/ ai/starmind/StarMind-main/tests/test_favorite_title_extractors.py`
  - 增加重复 DOM 链接只输出一条的测试。
  - 增加 `[我` 这类异常标题不被输出为有效收藏的测试。
  - 增加 profile 收藏链接、discovery 分享链接混合时按同一 note id 去重的测试。
- `/Users/wyy/ ai/starmind/StarMind-main/tests/test_xiaohongshu_diandian_extractor.py`
  - 增加 `click_at()` 必须被调用的测试。
  - 增加发送后消息数量未增加时返回失败的测试。
  - 增加回复稳定后才返回成功内容的测试。
- 可选新增或修改：`/Users/wyy/ ai/starmind/StarMind-main/tests/test_favorite_scan_entrypoints.py`
  - 覆盖 `/api/sync/scan-titles` 对小红书扫描结果不重复。

## 影响范围

- 小红书收藏页扫描：更严格过滤和去重。
- 小红书点点提取：发送动作更可靠，失败会暴露明确错误。
- B站 / 抖音 / 豆包流程：不应改变；回归测试需要覆盖。

## 边界条件

1. 如果小红书页面 DOM 改版导致无法识别卡片：扫描应返回空或明确错误，而不是返回页面 UI 噪声。
2. 如果点点输入框可填但发送按钮不可点击：接口应返回发送失败，不写入 RawSource。
3. 如果点点一直生成中或回复为空：接口应返回 timeout/empty_response，不写入 RawSource。
4. 如果同一笔记有多个 URL 形态：只保留一个候选，优先保留带标题和分享链接的版本。

## 验证方式

1. 先写失败测试并确认失败。
2. 实现最小修复。
3. 运行目标测试：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_favorite_title_extractors.py tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_favorite_scan_entrypoints.py tests/test_sync_favorites_page.py -v
```

4. 使用浏览器 CDP 验证小红书页面：
   - 扫描结果中同一标题不重复。
   - 不再出现 `[我` 这类异常条目。
   - 点点页面输入 prompt 后能点击发送并等待回复。

## 预期结果

- 小红书扫描列表中每条收藏笔记唯一。
- 异常 UI 文本不会进入扫描结果。
- 点点提取会真正点击发送箭头，等待生成完成后写入 RawSource。
- 失败时给出明确错误，避免用户误以为已提取成功。
