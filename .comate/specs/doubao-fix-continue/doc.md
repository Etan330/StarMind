# 同步收藏夹续跑状态与豆包发送闭环修复设计（StarMind 新版本）

## 需求场景

本次在新版本目录 `/Users/wyy/ ai/starmind/StarMind-main/StarMind` 上继续上一轮未完成工作。外层 `/Users/wyy/ ai/starmind/StarMind-main` 已视为旧版本，不再作为迭代目标。

需要修复三类问题：

1. `/ui/sync` 平台卡片顺序需要调整：抖音固定最上面，TikTok 固定最下面，并保持图标正确。
2. 抖音、小红书、B站同步收藏夹流程需要支持上次停留状态恢复。
   - 扫描标题后离开/刷新，下次恢复扫描结果，可继续 AI 分类。
   - AI 分类后离开/刷新，下次恢复分类结果，可继续勾选提取。
   - prepare-selected 后离开/失败，下次保留候选 ID，可继续提取。
3. 抖音、B站 selected extraction 发送到豆包链路需要闭环：稳定写入输入框、点击真实发送按钮、确认已发送、等待豆包生成完成，再写入 RawSource。小红书继续走点点链路。

## 当前新版本代码分析

### 项目根目录

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind
```

### `/ui/sync` 平台卡片

文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/api/routes.py
```

当前相关位置：

- `PLATFORM_PRESETS` 定义在 `app/api/routes.py:175` 附近。
- `favorite_platform_cards()` 定义在 `app/api/routes.py:601` 附近。
- 当前排序仍是 `sorted(PLATFORM_PRESETS, key=lambda item: int(item["priority"]))`。

新版本仍需要将 `/ui/sync` 展示排序调整为抖音第一、TikTok 最后，其它平台保持原 priority 相对顺序。

### 前端同步收藏夹流程

文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/static/app.js
```

当前 `[data-batch-title-filter]` 流程仍是页面内存状态：

- `renderGroups()` 在 `app/static/app.js:353` 附近。
- 扫描接口 `/api/sync/scan-titles` 在 `app/static/app.js:402` 附近。
- 分类接口 `/api/classify/batch-titles` 在 `app/static/app.js:432` 附近。
- prepare-selected 接口 `/api/sync/prepare-selected` 在 `app/static/app.js:461` 附近。
- 豆包 selected extraction 接口 `/api/doubao/extract-selected` 在 `app/static/app.js:468` 附近。

当前新版本未发现 localStorage 续跑状态逻辑，因此用户刷新页面或重新进入页面后，扫描/分类/候选 ID 会丢失。

### 豆包发送逻辑

文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/connectors/doubao_extractor.py
```

当前相关位置：

- `DoubaoExtractor.extract_content()` 在 `app/connectors/doubao_extractor.py:142` 附近。
- `_send_prompt()` 在 `app/connectors/doubao_extractor.py:199` 附近。
- `_wait_for_response_complete()` 在 `app/connectors/doubao_extractor.py:375` 附近。

新版本已有部分输入框 selector、`prompt_input_not_applied`、`click_at()` 逻辑，但仍存在关键缺口：

- `extract_content()` 当前调用 `_wait_for_response_complete(tab, before_count, timeout_seconds)`，没有把 prompt 传入等待逻辑。
- `_wait_for_response_complete()` 无法明确排除用户 prompt 本身，可能把用户发出的 prompt 当成回复内容。
- 仍需要检查发送确认条件是否足够严格，确保返回 `doubao_send_not_confirmed` 时不会误入库。

### 后端 selected extraction metadata

文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/api/routes.py
```

相关函数：

```text
extract_selected_with_doubao()
```

位置：`app/api/routes.py:3317` 附近。

需要确认并补齐：

- 单条失败时写入 `doubao_extracted=False`、`doubao_error`、`doubao_prompt`、`doubao_elapsed_seconds`。
- 单条成功时写入 `doubao_error=None`。
- 单条失败不阻断后续 candidate。

## 技术方案

采用「只在新版本目录落地」的最小闭环方案，不再修改外层旧版本。

### 方案一：平台卡片排序

在 `favorite_platform_cards()` 中使用专用排序 key：

```python
def favorite_platform_sort_key(item):
    platform = str(item["platform"])
    if platform == "douyin":
        return (-1, int(item["priority"]))
    if platform == "tiktok":
        return (1, int(item["priority"]))
    return (0, int(item["priority"]))
```

仅影响 `/ui/sync` 平台卡片列表，不调整平台配置含义。

### 方案二：前端 localStorage 续跑状态

在 `app/static/app.js` 的 `[data-batch-title-filter]` 初始化块中增加平台级状态：

```javascript
const resumablePlatforms = new Set(["douyin", "xiaohongshu", "bilibili"])
const stateKey = `starmind.batchTitleFilter.${platform}`
```

状态结构：

```json
{
  "version": 1,
  "platform": "douyin",
  "stage": "scanned | classified | prepared | completed",
  "homepageUrl": "...",
  "limit": "10",
  "scannedItems": [],
  "classifiedItems": [],
  "groups": [],
  "selectedCandidateIds": [],
  "summaryText": "...",
  "statusText": "...",
  "updatedAt": "..."
}
```

保存时机：

- 扫描成功：保存 `stage=scanned` 和 `scannedItems`，清空分类和候选 ID。
- 分类成功：保存 `stage=classified`、`classifiedItems`、`groups`。
- prepare-selected 成功：保存 `stage=prepared`、`selectedCandidateIds`。
- 提取完成：保存 `stage=completed` 和完成摘要，避免误导用户重复提取。

恢复逻辑：

- 初始化时安全读取 localStorage。
- 校验 `version`、`platform`、数组结构。
- `scanned`：恢复扫描预览，启用 AI 分类，禁用提取。
- `classified`：恢复分组勾选界面，启用提取。
- `prepared`：恢复分组和候选 ID，启用提取。
- `completed`：显示完成摘要，用户可重新扫描。
- 数据损坏时清理状态，不阻断页面。

### 方案三：豆包发送与等待闭环

继续在 `app/connectors/doubao_extractor.py` 中增强，不新增外部服务。

重点修改：

1. `_send_prompt()` 保持并补强：
   - 扩大输入框 selector。
   - 写入后验证输入框包含 prompt 头部或 URL。
   - 扩大发送按钮候选范围。
   - DOM click 后再使用 `CDPProxy.click_at()`。
   - 点击后确认 prompt 离开输入框或新消息出现 prompt/URL，否则返回 `doubao_send_not_confirmed`。
2. `_wait_for_response_complete()` 增加 `prompt` 参数：
   - 忽略包含 prompt 头部或目标 URL 的用户消息。
   - 仅在 message count 增加、回复文本足够长、非生成中、连续稳定后返回。
   - 如果最终文本仍是 prompt 或过短，返回空字符串。
3. `extract_content()` 调用等待函数时传入 prompt。

### 方案四：后端 metadata 增强

在 `extract_selected_with_doubao()` 中补齐失败和成功 metadata：

失败：

```python
{
    "doubao_extracted": False,
    "doubao_error": result.error or "empty_response",
    "doubao_prompt": result.prompt,
    "doubao_elapsed_seconds": result.elapsed_seconds,
}
```

成功：

```python
{
    "doubao_extracted": True,
    "doubao_error": None,
}
```

保持单条失败不阻断后续条目。

## 受影响文件

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/api/routes.py
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/static/app.js
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/connectors/doubao_extractor.py
/Users/wyy/ ai/starmind/StarMind-main/StarMind/tests/test_sync_favorites_page.py
/Users/wyy/ ai/starmind/StarMind-main/StarMind/tests/test_doubao_extract_selected.py
```

## 边界条件与异常处理

- 只修改新版本目录 `/StarMind`，不再修改旧版本目录。
- localStorage 数据损坏：清理并回到初始状态。
- 非 `douyin/xiaohongshu/bilibili` 平台：不启用续跑。
- 用户重新扫描：覆盖旧状态。
- 豆包未登录：保持当前 `doubao_login_required` 逻辑。
- 豆包输入框未就绪或写入失败：返回明确错误，不写 RawSource。
- 单条发送失败：记录失败 metadata，继续后续 candidate。
- 小红书点点链路不改为豆包。

## 数据流

### 续跑状态

```text
扫描成功
  -> localStorage stage=scanned
  -> 刷新/重新进入
  -> 恢复扫描结果
  -> 继续 AI 分类

分类成功
  -> localStorage stage=classified
  -> 刷新/重新进入
  -> renderGroups(groups)
  -> 继续勾选提取

prepare-selected 成功
  -> localStorage stage=prepared + selectedCandidateIds
  -> 提取失败或关闭页面
  -> 重新进入
  -> 复用 candidate_ids 继续提取
```

### 豆包提取

```text
用户勾选抖音/B站条目
  -> /api/sync/prepare-selected
  -> /api/doubao/extract-selected
  -> DoubaoExtractor.extract_content(url)
  -> _send_prompt(prompt)
      -> 写入输入框
      -> 点击真实发送按钮
      -> 确认已发送
  -> _wait_for_response_complete(prompt)
      -> 等待生成完成
      -> 排除用户 prompt
  -> RawSourceService.ingest_candidate(candidate.id)
```

## 预期结果

- 新版本 `/StarMind` 的 `/ui/sync` 展示抖音第一、TikTok 最后。
- 抖音、小红书、B站 source setup 页面刷新后可恢复扫描/分类/准备提取状态。
- 抖音和 B站 selected extraction 能逐条发送到豆包，并等待生成完成后入库。
- 豆包失败时有明确 metadata，且不会阻断后续条目。
- 小红书仍走点点链路。

## 验证计划

在新版本目录执行：

```bash
PYTHONPATH=. python -m py_compile app/api/routes.py app/connectors/doubao_extractor.py
PYTHONPATH=. pytest tests/test_sync_favorites_page.py tests/test_doubao_extract_selected.py -v
```

必要时补充：

```bash
PYTHONPATH=. pytest tests/test_batch_title_filtering.py tests/test_favorite_scan_entrypoints.py tests/test_favorite_title_extractors.py -v
```

浏览器验证：

- 重启或确认 8000 端口服务加载 `/StarMind` 目录代码。
- 打开 `/ui/sync`，确认抖音第一、TikTok 最后。
- 打开 `/ui/source-setup/douyin`，扫描后刷新，确认扫描结果恢复。
- 分类后刷新，确认分类结果恢复。
- 抖音/B站 selected extraction 确认豆包输入、发送、等待、入库闭环。
- 小红书确认仍使用点点链路。
