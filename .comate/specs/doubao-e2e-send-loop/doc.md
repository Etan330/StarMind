# 豆包勾选提取端到端发送入库闭环修复设计

## 需求场景

用户在新版本目录 `/Users/wyy/ ai/starmind/StarMind-main/StarMind` 中使用“仅提取我勾选的内容”时，期望完整闭环为：

```text
勾选抖音/B站内容
  -> 跳到豆包
  -> 逐条把该条链接和通用 Prompt 输入到豆包对话框
  -> 点击右下角发送箭头
  -> 等待豆包生成完成
  -> 提取豆包生成内容
  -> 写入 StarMind 原始资料 RawSource
```

用户反馈当前真实现象仍是：豆包打开后输入框没有最终发送内容，链接和 Prompt 没有进入对话，端到端没有完成。

## Web Access 实测证据

已使用 Web Access/CDP 对真实豆包页面验证，CDP 状态：

```text
node: ok (v24.14.1)
browser: ok (Chrome, port 9222)
proxy: ready (Chrome)
```

真实豆包页面地址：

```text
https://www.doubao.com/chat/
```

### 发送前页面状态

真实可见输入框：

```json
{
  "tag": "TEXTAREA",
  "className": "semi-input-textarea semi-input-textarea-autosize",
  "placeholder": "发消息或按住空格说话...",
  "text": "",
  "visible": true,
  "rect": {"left": 480, "top": 626, "width": 760, "height": 24}
}
```

右下角真实发送按钮在写入后出现：

```json
{
  "tag": "BUTTON",
  "className": "... !bg-g-send-msg-btn-bg ...",
  "disabled": false,
  "visible": true,
  "rect": {"left": 1214, "top": 664, "width": 36, "height": 36}
}
```

### 当前代码实测结果

调用当前 `_send_prompt()` 后返回：

```json
{
  "success": false,
  "error": "doubao_send_not_confirmed",
  "url": "https://example.com/starmind-web-access-e2e",
  "click_x": 1232,
  "click_y": 682
}
```

发送后输入框仍保留 Prompt：

```json
{
  "text": "StarMind 端到端验证：请读取这个测试链接并回复一小段确认文字。链接：https://example.com/starmind-web-access-e2e"
}
```

页面消息数量仍为 0，说明：

1. Prompt 写入一度成功进入 textarea。
2. 发送按钮定位到了正确的右下角按钮坐标区域。
3. 但现有点击策略没有真正触发豆包发送，Prompt 留在输入框中。
4. 因为没有发送成功，后续等待生成、提取内容和入库都不会发生。

本次新的根因不再是 `_message_state()` 的 `text` 未定义，而是豆包 textarea 受 React/Semi 组件控制，单纯 DOM setter + click 没有形成豆包前端认可的真实输入/发送动作。

## 技术方案

### 方案一：改造 `_send_prompt()` 为真实交互优先

受影响文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/connectors/doubao_extractor.py
```

受影响函数：

```text
DoubaoExtractor._send_prompt()
CDPProxy
```

当前 `_send_prompt()` 在页面内脚本里完成：

- 选择输入框
- paste/DOM setter 写入 prompt
- DOM click 发送按钮
- 返回坐标后 Python 侧再 `click_at()`

问题是：真实页面里 DOM click 和现有 `click_at()` 后输入框仍保留文本，消息没有进入对话区。

修复策略：

1. 将“写入”和“发送”拆成更可验证的步骤。
2. 写入后从页面返回输入框中心坐标、发送按钮中心坐标、输入框内容、按钮状态。
3. Python 侧使用 CDP 真实输入事件执行：
   - 聚焦输入框。
   - 选择旧内容。
   - 尝试粘贴/键盘输入完整 prompt。
   - 验证输入框包含 prompt 头部和 URL。
4. 发送阶段优先使用真实用户手势：
   - 点击右下角发送按钮坐标。
   - 如按钮点击后未发送，尝试 `Enter` 键。
   - 每次动作后轮询确认：输入框清空或页面新增用户消息包含 prompt/URL。
5. 只有确认发送成功后，才进入 `_wait_for_response_complete()`。

### 方案二：补齐 CDPProxy 键盘输入能力

当前 `CDPProxy` 只有 `eval_script()`、`click_at()` 等能力。为了模拟真实用户输入，需要在 `app/connectors/cdp_proxy.py` 增加最小键盘 API 包装，调用本地 cdp-proxy 的输入能力。如果当前 proxy 没有直接接口，则通过 `/eval` 聚焦元素后结合浏览器原生事件与坐标点击完成。

优先顺序：

1. 先检查 `/Users/wyy/.claude/skills/web-access/scripts/cdp-proxy.mjs` 是否已有键盘、type、press、paste 类接口。
2. 若已有接口，在 `CDPProxy` 增加对应方法。
3. 若没有接口，不改 proxy 脚本，改为在页面内用受控组件兼容方式触发：
   - focus textarea
   - setSelectionRange
   - dispatch `beforeinput` / `input` / `keyup`
   - 使用 `document.execCommand('insertText', false, prompt)`
   - 最后仍以页面可见 textarea 值为准验收。

### 方案三：等待生成必须以“发送成功”为前置

`extract_content()` 的闭环条件改为：

```text
_send_prompt() success=True
  -> _wait_for_response_complete()
  -> 回复文本稳定且非用户 Prompt
  -> 写 RawSource
```

如果 `_send_prompt()` 返回：

- `prompt_input_not_applied`
- `send_button_not_found`
- `send_button_disabled`
- `doubao_send_not_confirmed`

则该条不进入等待生成，不写 RawSource，只记录失败 metadata，继续下一条。

### 方案四：端到端验证标准

修复后必须用 Web Access/CDP 验证真实页面，而不是只跑单测。

验收条件：

1. 真实豆包 textarea 中出现完整 Prompt 和目标 URL。
2. 点击右下角发送箭头后，textarea 清空或对话区新增用户消息。
3. `_message_state()` 的 `count` 增加。
4. `_wait_for_response_complete()` 能等到非 Prompt 的豆包回复文本。
5. 调用 `/api/doubao/extract-selected` 后，数据库中新增 RawSource。
6. Candidate metadata 中：

```json
{
  "doubao_extracted": true,
  "doubao_error": null
}
```

## 受影响文件

预计受影响文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/connectors/doubao_extractor.py
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/connectors/cdp_proxy.py
/Users/wyy/ ai/starmind/StarMind-main/StarMind/tests/test_doubao_extract_selected.py
/Users/wyy/ ai/starmind/StarMind-main/StarMind/.comate/specs/doubao-e2e-send-loop/tasks.md
/Users/wyy/ ai/starmind/StarMind-main/StarMind/.comate/specs/doubao-e2e-send-loop/summary.md
```

如果确认 cdp-proxy.mjs 已有键盘接口但 Python wrapper 未暴露，则只修改 `cdp_proxy.py` wrapper，不修改 skill 目录或外部 proxy 脚本。

## 边界条件与异常处理

- 豆包未登录：返回 `doubao_login_required`，不写 RawSource。
- 输入框不可见：返回 `chat_input_not_ready`。
- 写入后 textarea 不含 prompt/URL：返回 `prompt_input_not_applied`。
- 发送按钮不可见或禁用：返回 `send_button_not_found` 或 `send_button_disabled`。
- 点击和 Enter 后仍没有新增用户消息/清空输入框：返回 `doubao_send_not_confirmed`。
- 豆包生成超时：返回 `豆包未返回完整内容（超时）`。
- 单条失败不阻断后续勾选条目。

## 预期结果

修复完成后，用户点击“仅提取我勾选的内容”时，抖音/B站每条候选都会按顺序完成：

```text
Prompt + URL 写入豆包
  -> 真实点击发送
  -> 等待回复稳定
  -> 读取回复内容
  -> 写入 RawSource
```

不允许只打开豆包、不发送、不等待、不入库。
