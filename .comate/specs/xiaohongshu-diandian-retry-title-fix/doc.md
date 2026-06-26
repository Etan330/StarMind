# 小红书点点提取重试、批处理完整性与原始资料标题展示修复

## 背景与问题

用户在小红书收藏页勾选多条内容后执行「仅提取我勾选的内容」，反馈两个问题：

1. 点点提取不稳定：
   - 第一条发送后，点点回复：`这个问题我暂时还没有好的思路，换个问题试试吧`。
   - 这类回复不应视为成功提取，系统应重新发送同一条分享文本和 prompt 再试一次。
   - 最多重试一次，不进行第三次。
   - 用户一共选择 4 条，但实际只处理了前三条；第一条还是无效回答，第四条没有输入发送，点点窗口就被关闭，最终表现为成功 3 个、失败 1 个。
2. 原始资料页面标题展示不符合预期：
   - `/ui/sources` 的「收藏夹」列表和右侧详情页标题显示成原始链接或 URL 主题。
   - 用户希望显示收藏内容的真实主题标题，例如小红书笔记标题、B站视频标题、抖音视频标题。
   - 点击进入详情后，一级标题也应是该内容标题，而不是 URL。

## 当前实现分析

### 点点提取链路

相关文件：

- `/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`
- `/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu_diandian_extractor.py`
- `/Users/wyy/ ai/starmind/StarMind-main/app/services/raw_source_service.py`

当前关键逻辑：

- `/api/xiaohongshu/diandian/extract-selected` 创建一个 `XiaohongshuDiandianExtractor()`。
- 对 `candidate_ids` 逐条循环：
  - 构造分享文本。
  - 调用 `extractor.extract_content(...)`。
  - 成功则把点点返回文本写入 candidate metadata。
  - 调用 `RawSourceService.ingest_candidate(candidate.id)` 写入 RawSource。
- 最后在 `finally` 中调用 `extractor.close(close_tab=not keep_diandian_tab_open)`。

当前 `XiaohongshuDiandianExtractor.extract_content()`：

- 发送 prompt。
- 等待 `_wait_for_response_complete()` 返回内容。
- 只要 `content` 非空，就返回 `success=True`。
- 没有识别「这个问题我暂时还没有好的思路，换个问题试试吧」这类无效答复。
- 没有对无效答复自动重试。

### 可能导致第四条未处理的点

需要在实现阶段进一步通过测试确认，但从代码结构看可能有几类原因：

1. 如果点点页面状态或发送确认异常，当前单条失败理论上会进入 `failed_count` 并继续循环下一条；但如果某处抛出未捕获异常，则可能提前进入 `finally` 并关闭点点窗口。
2. `extractor.close()` 在整个接口结束时关闭 tab；如果循环提前结束或异常提前跳出，就会在第四条还没发送前关闭窗口。
3. 前端只看到接口返回 `200`，但如果结果中 `failed_count=1`、`items` 里只有 3 个成功，UI 当前提示不够明确，用户会感知为“成功了 3 个失败 1 个，但第四个没发”。

修复方向：确保单条无效回复只影响该条、不会中断后续条目；确保每个 candidate 都被尝试处理；在结果中记录每条 attempts 和失败原因。

### 原始资料标题链路

相关文件：

- `/Users/wyy/ ai/starmind/StarMind-main/app/services/raw_source_service.py`
- `/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`
- `/Users/wyy/ ai/starmind/StarMind-main/app/templates/sources.html`

当前 `RawSourceService.ingest_candidate()` 使用：

```python
title=candidate.title
```

详情页 `sources.html` 使用：

```jinja2
<strong>{{ source.title }}</strong>
<h2>{{ selected_source.title }}</h2>
<pre>{{ selected_transcript ... }}</pre>
```

如果 `candidate.title` 是 URL，就会导致列表和详情标题都是 URL。

对于点点提取接口，目前 `XiaohongshuDiandianExtractor` 的 result title 是：

```python
title=url.split("/")[-1][:60] if url else ""
```

这也是 URL 片段，不适合作为内容标题。接口写 RawSource 前没有把 candidate title 修正为用户在扫描阶段看到的标题，或从 metadata/share_text 中恢复标题。

## 修复目标

### 点点提取稳定性

1. 点点返回以下无效答复时，不写 RawSource，不标记成功：
   - `这个问题我暂时还没有好的思路，换个问题试试吧`
   - 语义等价的「没有思路 / 换个问题 / 暂时无法回答」短答复
2. 对每条小红书候选最多尝试 2 次：
   - 第 1 次无效答复或空答复时，再发送同一条 prompt 和分享链接 1 次。
   - 第 2 次仍无效，则标记该条失败。
   - 不进行第 3 次。
3. 批处理必须完整遍历用户勾选的所有 candidate：
   - 4 条就必须逐条尝试 4 条。
   - 单条失败不能提前终止整个批次，除非点点登录失效或页面不可用这类全局错误。
4. 点点 tab 只在整个批次结束后关闭；如果中间某条失败，不应导致后续条目未发送。
5. API 响应和 metadata 记录每条 attempts、失败原因、是否因无效答复重试。

### 原始资料标题展示

1. RawSource 标题优先使用收藏扫描阶段的真实标题。
2. 如果 candidate title 是 URL 或明显不是标题，则尝试从 metadata 中恢复：
   - `xiaohongshu_diandian_share_text`
   - `xiaohongshu_share_text`
   - `title`
   - `yt_dlp_title`
3. 对小红书分享文本，从 `【标题 | 小红书 - 你的生活兴趣社区】 ...` 提取 `标题`。
4. B站、抖音同样沿用候选标题或 metadata 标题，避免把 raw/canonical URL 作为 RawSource title。
5. `/ui/sources` 收藏夹列表和详情页 `<h2>` 显示真实标题；详情里的 transcript 一级标题也使用真实标题。

## 技术方案

### 1. 增加点点无效答复识别

修改文件：

`/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu_diandian_extractor.py`

新增函数：

```python
def is_unhelpful_diandian_response(text: str) -> bool:
    ...
```

识别规则：

- 去空白后文本较短。
- 包含以下模式：
  - `暂时还没有好的思路`
  - `换个问题试试`
  - `暂时无法回答`
  - `没有好的思路`
- 如果匹配，视为无效回复。

### 2. extract_content 内置最多 2 次尝试

修改 `extract_content()`：

- 增加参数：`max_attempts: int = 2`。
- 循环 attempts：
  1. 记录发送前消息状态。
  2. 发送 prompt。
  3. 等待回复。
  4. 如果空或无效，且 attempts < max_attempts，则重新发送同一 prompt。
  5. 成功时返回 `success=True`，并写入 `attempts`、`retried`。
  6. 两次都失败则返回 `success=False`，`error="xiaohongshu_diandian_unhelpful_response"` 或 timeout。

扩展 `DiandianExtractResult`：

```python
attempts: int = 1
retried: bool = False
```

### 3. 后端批处理完整性增强

修改文件：

`/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`

在 `/api/xiaohongshu/diandian/extract-selected` 中：

- 对每个 candidate 保持 try/except 包裹，单条异常只记录到 `failed_items`，继续下一条。
- 只有全局 `check_ready()` 失败时返回 428。
- metadata 增加：
  - `xiaohongshu_diandian_attempts`
  - `xiaohongshu_diandian_retried`
  - `xiaohongshu_diandian_error`
- API response 中每条 item 返回 attempts/error，便于前端展示。

### 4. RawSource 标题规范化

修改文件：

`/Users/wyy/ ai/starmind/StarMind-main/app/services/raw_source_service.py`

新增标题规范化函数：

```python
def _display_title(candidate: CandidateItem, metadata: dict[str, Any]) -> str:
    ...
```

优先级：

1. `candidate.title`，前提是非 URL、非空、非 `未识别标题`。
2. metadata 中：
   - `title`
   - `yt_dlp_title`
   - `xiaohongshu_title`
3. 从 `xiaohongshu_diandian_share_text` 或 `xiaohongshu_share_text` 提取 `【... | 小红书 - 你的生活兴趣社区】` 内的标题。
4. fallback 到 `candidate.title`。
5. 最后 fallback 到 canonical/raw URL。

应用位置：

- `RawSource.title`
- `_build_transcript()` 的 `# {title}`
- `_build_raw_text()` 的 `# 原始资料：{title}`

### 5. 测试方案

新增/修改测试：

- `tests/test_xiaohongshu_diandian_extractor.py`
  - 点点第一次返回无效短答复时，第二次重新发送，最终成功。
  - 两次都返回无效短答复时，返回失败且 attempts=2。
- `tests/test_xiaohongshu_diandian_extract_selected.py`
  - 4 个 candidate 中第 1 个重试成功，第 4 个也被处理，接口返回 success_count=4。
  - 单条失败不阻断后续 candidate。
- 新增或扩展 RawSource 标题测试：
  - candidate.title 是 URL，但 metadata/share_text 有标题时，RawSource.title 使用标题。
  - transcript 一级标题使用真实标题。
  - B站/抖音 candidate 有标题时不被 URL 覆盖。

## 影响范围

- 小红书点点提取稳定性。
- 小红书批量提取完整性。
- RawSource 标题生成逻辑。
- `/ui/sources` 展示会自动使用更准确的 RawSource.title。

## 边界条件

1. 两次点点都回复无效短答复：该条失败，但继续处理后续条目。
2. 点点页面不可用或登录失效：这是全局错误，返回 428，不进入批处理。
3. candidate.title 本身就是正确标题：不改动。
4. metadata 无法恢复标题：才 fallback 到 URL。
5. 已存在 RawSource 的旧数据不会自动迁移，除非额外执行修复脚本；本次优先保证新入库正确。

## 验证方式

1. 先写失败测试并确认失败。
2. 实现修复。
3. 运行目标测试：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_doubao_extract_selected.py tests/test_favorite_title_extractors.py -v
```

4. 浏览器验证：
   - `/ui/sources` 收藏夹列表显示标题而不是链接。
   - 详情页 `<h2>` 显示标题。
   - 点点无效答复时最多重试一次。

## 预期结果

- 选择 4 条提取时，系统会逐条尝试 4 条，不提前关闭点点窗口。
- 第一条如果点点返回无效答复，会自动再发一次；第二次成功则入库成功。
- 如果第二次仍失败，该条明确失败，但后续条目继续处理。
- 原始资料列表和详情页展示真实内容标题，而不是 URL。
