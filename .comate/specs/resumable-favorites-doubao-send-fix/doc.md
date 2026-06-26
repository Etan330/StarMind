# 同步收藏夹续跑状态与豆包发送闭环修复设计

## 需求场景

本次修复覆盖三个问题：

1. `/ui/sync` 平台卡片顺序需要调整：抖音移到最上面，TikTok 移到最下面，并补齐/保持对应图标。

2. 抖音、小红书、B站同步收藏夹流程需要支持「上次停留状态恢复」。
   - 如果用户只完成了「扫描标题」，未继续分类，下次从同一入口进入时应看到上次扫描结果，并可继续点击 AI 分类。
   - 如果用户完成了「AI 分类」，未继续提取，下次进入时应直接看到分类结果，并可继续勾选后提取。
   - 如果用户完成了「创建候选 / 准备提取」，但未完成提取，也应保留可继续提取所需的候选 ID。
   - 目标平台：`douyin`、`xiaohongshu`、`bilibili`。

3. 抖音、B站 selected extraction 发送到豆包的链路不完整。
   - 现象：点击「仅提取我勾选的内容」后，系统应把每条链接和 prompt 逐条发给豆包，但当前没有稳定写入豆包输入框，也没有稳定发送、等待生成完成再提取结果。
   - 要求：复用之前小红书点点修复经验，明确写入输入框、点击真实发送按钮、确认消息已发出、等待豆包生成完成，再写入 RawSource。
   - 目标平台：`douyin`、`bilibili` 共用豆包逻辑；小红书继续使用点点逻辑。

## 当前代码分析

### 前端批量流程

文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/app/static/app.js
```

当前同步收藏夹过滤逻辑位于 `document.querySelectorAll("[data-batch-title-filter]")` 初始化块中。

关键状态只保存在页面内存变量：

```javascript
let scannedItems = []
let classifiedItems = []
let selectedCandidateIds = []
```

因此用户退出页面或刷新页面后，状态丢失。这是续跑问题的直接原因。

当前三个关键按钮流程：

```javascript
/api/sync/scan-titles
/api/classify/batch-titles
/api/sync/prepare-selected
/api/doubao/extract-selected 或 /api/xiaohongshu/diandian/extract-selected
```

### 后端批量接口

文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py
```

当前已有：

- `POST /api/sync/scan-titles`
- `POST /api/classify/batch-titles`
- `POST /api/sync/prepare-selected`
- `POST /api/xiaohongshu/diandian/extract-selected`
- `POST /api/doubao/extract-selected`

但没有保存「扫描结果 / 分类结果 / 已准备候选」的 session/draft 状态，也没有恢复接口。

### 豆包发送逻辑

文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/app/connectors/doubao_extractor.py
```

当前 `_send_prompt()` 已尝试：

- 查找输入框。
- 使用 paste/DOM 写入 prompt。
- 查找按钮并点击。
- 如果有 click 坐标，再调用 `CDPProxy.click_at()`。
- 通过 message count / page text 粗略判断是否发送。

但与小红书点点已经修复过的逻辑相比，仍存在几个风险点：

1. 写入验证只读 `value / innerText / textContent`，豆包若使用 ProseMirror/slate 等编辑器时，DOM 写入可能看似执行但未进入框架状态。
2. 按钮选择只查找 `button, [role="button"]`，如果豆包发送箭头是 div/svg 包裹或按钮 class 已变化，可能找不到或点错。
3. 点击后确认条件过弱：`after_count <= before_count && url not in page_text` 才失败；如果页面已有旧消息或 URL 在页面其他位置，可能误判已发送。
4. 等待回复只看最后 message 文本和稳定轮次，未排除用户 prompt 本身，可能把用户发出的 prompt 当作返回内容。
5. selected extraction 后端未记录每条豆包发送失败的详细 metadata，不利于续跑和定位。

## 技术方案

### 方案选择

采用「平台卡片排序微调 + 前端 localStorage 续跑 + 后端豆包发送增强」的最小闭环方案。

原因：

- 用户明确要求恢复同一入口下上一次未完成步骤；当前缺失是页面内存状态丢失，用 localStorage 保存平台级流程状态即可满足。
- 不新增数据库表，避免迁移成本。
- 不改变现有 API 数据模型。
- 豆包发送问题属于浏览器自动化闭环不稳，应在 `DoubaoExtractor` 内修复根因，与抖音/B站共用。

## 需求一：`/ui/sync` 平台卡片顺序与图标

当前 `/ui/sync` 的卡片数据来自 `app/api/routes.py` 中 `PLATFORM_PRESETS`，渲染模板为 `app/templates/sync_favorites.html`。

本次明确调整：

- 抖音卡片移到最上面。
- TikTok 卡片移到最下面。
- 抖音保留白底可见图标。
- TikTok 保留自身图标，不误改为抖音。

实现方式：仅修改 `/ui/sync` 使用的卡片排序逻辑，不调整 DOM 层级，不新增/删除平台卡片。

排序规则建议：

```python
def favorite_platform_sort_key(item):
    platform = str(item["platform"])
    if platform == "douyin":
        return (-1, int(item["priority"]))
    if platform == "tiktok":
        return (1, int(item["priority"]))
    return (0, int(item["priority"]))
```

这样抖音稳定在第一位，TikTok 稳定在最后，其它平台按原 priority 相对排序。

## 需求二：同步收藏夹续跑状态

### 状态存储键

在 `app/static/app.js` 中为每个平台保存一个 localStorage key：

```javascript
const stateKey = `starmind.batchTitleFilter.${platform}`
```

仅对以下平台启用：

```javascript
const resumablePlatforms = new Set(["douyin", "xiaohongshu", "bilibili"])
```

### 状态结构

保存当前完成步骤和数据：

```javascript
{
  "version": 1,
  "platform": "douyin",
  "stage": "scanned" | "classified" | "prepared" | "completed",
  "homepageUrl": "...",
  "limit": "10",
  "scannedItems": [...],
  "classifiedItems": [...],
  "groups": [...],
  "selectedCandidateIds": [1, 2, 3],
  "summaryText": "...",
  "statusText": "...",
  "updatedAt": "2026-06-25T..."
}
```

### 保存时机

1. 扫描成功后：
   - `stage = "scanned"`
   - 保存 `scannedItems`。
   - 清空 `classifiedItems`、`selectedCandidateIds`。
   - 页面恢复时显示扫描预览，启用「AI 分类」，禁用「提取」。

2. 分类成功后：
   - `stage = "classified"`
   - 保存 `classifiedItems` 和 `groups`。
   - 页面恢复时调用 `renderGroups(groups)`，显示分类结果，启用「提取」。

3. prepare-selected 成功后：
   - `stage = "prepared"`
   - 保存 `selectedCandidateIds`。
   - 如果提取接口失败或用户关闭页面，下次进入时仍可继续提取。

4. 提取完成后：
   - `stage = "completed"`
   - 保存完成摘要，或清理未完成状态。
   - 为避免误导用户重复操作，建议保留完成摘要但禁用继续提取，用户可重新扫描开始新流程。

### 恢复逻辑

页面初始化时：

1. 读取 localStorage。
2. 校验 `version`、`platform`、数据结构。
3. 根据 `stage` 恢复 UI：
   - `scanned`：显示扫描预览，启用分类。
   - `classified`：显示分类分组和勾选框，启用提取。
   - `prepared`：显示分类分组，启用提取，并复用候选 ID。
   - `completed`：显示完成摘要，可重新扫描。
4. 如果 localStorage 数据损坏，忽略并清理。

### 用户重新扫描

用户点击「扫描标题」时，认为是开始新流程：

- 清理旧 `classifiedItems`、`selectedCandidateIds`。
- 扫描成功后覆盖保存新状态。

## 需求三：豆包发送闭环修复

### 发送目标范围

后端 `/api/doubao/extract-selected` 被抖音和 B站共用。因此只需要修复：

```text
/Users/wyy/ ai/starmind/StarMind-main/app/connectors/doubao_extractor.py
```

以及必要的 selected extraction metadata：

```text
/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py
```

小红书继续走：

```text
/api/xiaohongshu/diandian/extract-selected
```

不改为豆包。

### 输入框写入策略

参考已验证的小红书点点 `_send_prompt()`，增强豆包写入：

1. 扩大输入框选择器：
   - `textarea:not([readonly])`
   - `input[type="text"]:not([readonly])`
   - `[contenteditable="true"]`
   - `[role="textbox"]`
   - `.ProseMirror`
   - `[data-testid*="chat"] textarea`
   - `[class*="editor"][contenteditable="true"]`
   - `[class*="input"] [contenteditable="true"]`

2. 优先选择可见、可编辑、靠近页面底部或 chat composer 的输入框。

3. 写入方法按顺序尝试：
   - focus + select all + paste event。
   - framework setter / DOM value setter。
   - `document.execCommand("insertText", false, prompt)`。
   - `textContent` fallback。

4. 写入后必须验证输入框文本包含 prompt 头部或目标 URL。未验证成功直接返回：

```text
prompt_input_not_applied
```

### 发送按钮选择策略

扩大候选元素，不只查 button：

```javascript
button,
[role="button"],
[class*="send"],
[class*="submit"],
[class*="Send"],
[class*="Submit"],
svg,
svg use
```

打分规则：

- 靠近输入框右侧/右下方加高分。
- class/aria/title/use href 含 `send`、`submit`、`arrow`、`plane` 加分。
- 尺寸类似 24-56px 图标按钮加分。
- 文案含「发送」加分。
- 文案含「上传」「附件」「图片」「语音」「更多」「深度」「搜索」扣分。

点击策略：

1. 先用 DOM `click()`。
2. 记录按钮中心坐标。
3. 再用 `CDPProxy.click_at()` 真实鼠标点击一次。
4. 点击后进入确认循环。

### 发送确认策略

发送后不能只看 message count。需要确认：

- 输入框中不再保留 prompt 头部，或
- 页面新消息中出现 prompt 头部 / 目标 URL，或
- message count 增加并最后消息不是旧内容。

如果多轮确认仍失败，返回：

```text
doubao_send_not_confirmed
```

### 等待豆包生成完成

增强 `_wait_for_response_complete()`：

- 传入 `prompt`。
- 忽略包含 prompt 头部的用户消息。
- 仅当 message count 大于发送前 count，且文本长度足够，且非生成中，且连续稳定若干轮后返回。
- 若最后文本仍是 prompt 或过短，返回空字符串。

### 批处理不中断

当前 `/api/doubao/extract-selected` 对每条 candidate 独立处理，单条失败会继续后续条目。保留该模式。

补充 metadata：

失败时写入 candidate metadata：

```python
{
  "doubao_extracted": False,
  "doubao_error": result.error or "empty_response",
  "doubao_prompt": result.prompt,
  "doubao_elapsed_seconds": result.elapsed_seconds,
}
```

成功时保留现有 metadata，并增加：

```python
"doubao_error": None
```

这样后续恢复/诊断可以知道是否已失败或已成功。

## 受影响文件

### `/Users/wyy/ ai/starmind/StarMind-main/app/static/app.js`

修改类型：前端状态保存与恢复。

受影响函数：

- `[data-batch-title-filter]` 初始化块。
- `renderGroups()`。
- 扫描按钮 click handler。
- 分类按钮 click handler。
- 提取按钮 click handler。

新增逻辑：

- localStorage read/write/clear helpers。
- 扫描预览渲染 helper。
- 初始化时按 stage 恢复 UI。

### `/Users/wyy/ ai/starmind/StarMind-main/app/connectors/doubao_extractor.py`

修改类型：浏览器自动化发送与等待逻辑修复。

受影响函数：

- `_send_prompt()`。
- `_wait_for_response_complete()`。
- `extract_content()` 调用等待函数时传入 prompt。

重点修复：

- 输入框写入验证。
- 发送按钮定位与真实点击。
- 发送确认。
- 等待回复时排除用户 prompt。

### `/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`

修改类型：豆包 selected extraction metadata 增强。

受影响函数：

- `extract_selected_with_doubao()`。

重点修复：

- 单条失败时写入 `doubao_extracted=False`、`doubao_error`、`doubao_prompt`。
- 单条成功时写入 `doubao_error=None`。
- 保持抖音/B站走豆包，小红书走点点。

### 测试文件

新增或更新：

```text
/Users/wyy/ ai/starmind/StarMind-main/tests/test_doubao_extract_selected.py
/Users/wyy/ ai/starmind/StarMind-main/tests/test_batch_resume_state_frontend.py 或现有前端相关测试
```

如果项目没有前端 JS 单测基础，则至少通过后端单测覆盖豆包失败 metadata、批处理不中断、prompt 传递，并通过浏览器 CDP 验证续跑 UI。

## 边界条件与异常处理

- localStorage 数据异常：清理并回到初始状态，不阻断页面。
- 平台不是 `douyin/xiaohongshu/bilibili`：不启用续跑逻辑。
- 用户重新扫描：覆盖旧状态。
- 豆包未登录：保持当前 428 `doubao_login_required` 逻辑，并保留豆包 tab 打开。
- 豆包输入框未就绪：返回明确错误，不写入 RawSource。
- 单条发送失败：记录失败 metadata，继续后续 candidate。
- 小红书点点链路不受豆包修复影响。

## 数据流

### 续跑状态

```text
扫描成功
  -> scannedItems 写入 localStorage(stage=scanned)
  -> 用户离开页面
  -> 再次进入 /ui/source-setup/{platform}
  -> app.js 读取 localStorage
  -> 恢复扫描结果和下一步按钮

分类成功
  -> groups/classifiedItems 写入 localStorage(stage=classified)
  -> 用户离开页面
  -> 再次进入
  -> renderGroups(groups)
  -> 用户继续勾选并提取

prepare-selected 成功
  -> selectedCandidateIds 写入 localStorage(stage=prepared)
  -> 提取失败或用户离开
  -> 再次进入
  -> 复用 selectedCandidateIds 继续提取
```

### 豆包提取

```text
用户勾选抖音/B站条目
  -> /api/sync/prepare-selected
  -> candidate_ids
  -> /api/doubao/extract-selected
  -> DoubaoExtractor.extract_content(url)
  -> _send_prompt(prompt)
      -> 写入豆包输入框
      -> 点击真实发送按钮
      -> 确认 prompt 已发送
  -> _wait_for_response_complete(prompt)
      -> 等待生成停止且回复稳定
      -> 排除用户 prompt
  -> RawSourceService.ingest_candidate(candidate.id)
```

## 预期结果

- 抖音、小红书、B站同步收藏夹页面可以恢复上次未完成的扫描/分类/准备提取状态。
- 用户不需要因为页面关闭或跳转而重新扫描、重新分类。
- 抖音和 B站 selected extraction 会逐条把 prompt + 链接发送到豆包。
- 豆包发送失败时能明确报错并保留上下文；不会误判成功。
- 豆包生成完成后再写入 RawSource。
- 小红书仍使用点点，不回退到豆包。

## 验证计划

1. 后端测试：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_doubao_extract_selected.py -v
```

2. 相关回归：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extract_selected.py tests/test_doubao_extract_selected.py tests/test_sync_favorites_page.py -v
```

3. Python 编译：

```bash
PYTHONPATH=. .venv311/bin/python -m py_compile app/api/routes.py app/connectors/doubao_extractor.py
```

4. 浏览器验证：

- 打开 `/ui/source-setup/douyin`，扫描后刷新/离开再进入，应恢复扫描结果。
- 分类后刷新/离开再进入，应恢复分类结果和勾选状态。
- 抖音 selected extraction 应打开/复用豆包页面，输入框出现 prompt，点击发送，等待生成完成并写入 RawSource。
- B站执行同样链路。
- 小红书仍跳转/复用点点页面。