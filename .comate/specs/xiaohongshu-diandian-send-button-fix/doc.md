# 小红书点点发送按钮误点加号修复

## 问题背景

用户反馈：在小红书收藏页点击「仅提取我勾选的内容」后，系统能跳转到小红书点点并把 prompt/分享链接输入到对话框，但没有点击右下角发送箭头，而是一直点击左下角加号，导致消息未发送、点点未生成回答、RawSource 未写入。

## 浏览器自动化定位结果

已通过 CDP 打开并检查 `https://www.xiaohongshu.com/ai_chat` 的真实 DOM。

### 输入框位置

真实输入框：

```text
textarea[name="aiSearchTextarea"].textarea
rect: left=411 top=283 right=1193 bottom=324
```

### 左下角加号

左下角加号是真实 `button`：

```text
button.ai-input-action-btn
use: #addM
rect: left=403 top=327 right=435 bottom=359
```

### 右下角发送箭头

右下角发送箭头不是 `button`，而是 `div`：

```text
div.submit-button-wrapper
use: #arrow_top
rect: left=1161 top=327 right=1193 bottom=359
```

其内部 SVG：

```text
svg.submit-button.btn-wrapper
use: #arrow_top
rect: left=1168 top=334 right=1186 bottom=352
```

## 根因

当前 `app/connectors/xiaohongshu_diandian_extractor.py` 的 `_send_prompt()` 只从以下节点中找发送按钮：

```javascript
document.querySelectorAll('button, [role="button"]')
```

真实发送箭头是 `div.submit-button-wrapper`，不在候选集中。因此当前打分逻辑只能看到左下角加号 `button.ai-input-action-btn`，并错误点击它。

这解释了用户看到的现象：系统输入了 prompt，但一直点左下角加号，没有点右下角箭头。

## 修复目标

1. 点点发送按钮选择必须优先选择右下角 `#arrow_top` / `.submit-button-wrapper`。
2. 明确排除左下角 `#addM` 加号。
3. 候选节点不再只限于 `button/[role=button]`，需要包含真实可点击 `div`/`svg` 容器。
4. 点击坐标必须落在右下角箭头区域，不能落在左下角加号区域。
5. 发送后继续做确认：输入框清空、消息数量增加或用户消息出现后才算发送成功。
6. 保持点点回复等待逻辑：等待点点回复稳定后再写 RawSource。

## 技术方案

### 1. 修改点点发送按钮定位逻辑

修改文件：

`/Users/wyy/ ai/starmind/StarMind-main/app/connectors/xiaohongshu_diandian_extractor.py`

在 `_send_prompt()` 的 JS 中：

- 候选节点从：

```javascript
button, [role="button"]
```

扩展为：

```javascript
button, [role="button"], .submit-button-wrapper, .submit-button, .bottom-box-right, .bottom-box-right-submit-button, svg.submit-button
```

- 提取 `use[href]` / `use[xlink:href]`，优先识别：

```text
#arrow_top
```

- 明确排除：

```text
#addM
ai-input-action-btn
bottom-box-left
```

- 打分策略：
  - `#arrow_top` 加高分。
  - `.submit-button-wrapper` 加高分。
  - 位于输入框右下方、`rect.left >= inputRect.right - 80` 加高分。
  - `#addM`、`ai-input-action-btn`、位于输入框左下方大幅扣分。

### 2. 点击目标使用最外层可点击容器

真实可点击容器为：

```text
div.submit-button-wrapper
```

点击坐标应取其中心点：

```text
x ≈ 1177, y ≈ 343
```

不是加号中心：

```text
x ≈ 419, y ≈ 343
```

### 3. 增加测试覆盖

修改文件：

`/Users/wyy/ ai/starmind/StarMind-main/tests/test_xiaohongshu_diandian_extractor.py`

新增测试：

- 当 DOM 同时存在左下角 `#addM` 加号 button 和右下角 `#arrow_top` div 时，选择 `#arrow_top`。
- 返回的点击坐标必须在输入框右侧区域，例如 `x > 1100`。
- 不允许返回加号坐标，例如 `x < 500`。

## 影响范围

- 只影响小红书点点提取器的发送按钮定位逻辑。
- 不影响小红书扫描、豆包提取、RawSource 写入接口。

## 验证方式

1. 先运行新增测试，确认旧逻辑失败。
2. 修改 `_send_prompt()` 按真实 DOM 定位发送箭头。
3. 运行：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py -v
```

4. 用浏览器自动化再次检查点点页面：
   - `#addM` 加号坐标仍在左下角。
   - `#arrow_top` 发送箭头坐标在右下角。
   - 新选择逻辑返回右下角箭头坐标。

## 预期结果

- 点击「仅提取我勾选的内容」后，系统输入 prompt，并点击右下角发送箭头。
- 不再点击左下角加号。
- 点点开始生成回答后，系统等待回复稳定并写入 RawSource。
