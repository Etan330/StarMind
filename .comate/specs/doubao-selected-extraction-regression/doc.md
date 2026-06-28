# 豆包勾选提取未写入 Prompt 回归修复设计

## 需求场景

用户在 `/Users/wyy/ ai/starmind/StarMind-main/StarMind` 新版本中使用“仅提取我勾选的内容”时，抖音/B站条目会跳转到豆包页面，但豆包对话框中没有写入链接和 prompt，也没有点击右下角发送按钮，最终没有等待豆包生成结果并写入原始资料，页面显示：

```text
完成：成功入库 0 条，失败 2 条。
```

本次修复只针对新版本目录：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind
```

不修改外层旧版本。

## 根因分析

### 失败数据

本地 SQLite 中最近失败的候选条目 metadata 已记录：

```json
{
  "doubao_extracted": false,
  "doubao_error": "eval failed: {\"error\":\"Uncaught\"}",
  "doubao_prompt": "请打开并解析下面这个链接...链接：https://www.douyin.com/...",
  "doubao_elapsed_seconds": 2.0
}
```

这说明失败发生在豆包浏览器自动化脚本执行阶段，而不是 RawSource 写入阶段。

### 直接复现链路

`DoubaoExtractor._send_prompt()` 在真正写入输入框前会先调用：

```python
before = await self._message_state(tab)
```

当前 `_message_state()` 中注入到页面的 JavaScript 为：

```javascript
(() => {
    const nodes = Array.from(document.querySelectorAll('[class*="message"], [class*="markdown"], [data-testid*="message"], [class*="answer"]'));
    const visible = nodes.map((node) => (node.innerText || '').trim()).filter(Boolean);
    const last = visible.length ? visible[visible.length - 1] : '';
    const controls = Array.from(document.querySelectorAll('button, [role="button"], [class*="loading"], [class*="stop"], [class*="spinner"], [class*="generat"]'));
    const generating = controls.some((node) => /停止|stop|生成中|思考中|正在生成|loading|spinner/i.test(node.innerText || node.getAttribute('aria-label') || node.className || ''));
    const canCopyOrRegenerate = controls.some((node) => /复制|copy|重新生成|regenerate/i.test(node.innerText || node.getAttribute('aria-label') || node.className || ''));
    return JSON.stringify({count: visible.length, text: last, page_text: text.slice(-4000), generating: generating && !canCopyOrRegenerate});
})()
```

其中 `page_text: text.slice(-4000)` 引用了未定义变量 `text`。因此页面 eval 抛出 `Uncaught`，`_send_prompt()` 在写入 prompt 前就异常退出，造成用户看到的“豆包打开了，但对话框根本没有内容”。

## 处理逻辑

### 1. 修复 `_message_state()` 的未定义变量

受影响文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/connectors/doubao_extractor.py
```

受影响函数：

```text
DoubaoExtractor._message_state()
```

将页面全文单独定义为 `pageText`，并在返回值中使用该变量：

```javascript
const pageText = document.body?.innerText || '';
return JSON.stringify({
    count: visible.length,
    text: last,
    page_text: pageText.slice(-4000),
    generating: generating && !canCopyOrRegenerate
});
```

这样 `_send_prompt()` 可以继续执行输入框选择、prompt 写入、发送按钮定位和发送确认。

### 2. 增加回归测试覆盖 `_message_state()` 脚本

受影响文件：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/tests/test_doubao_extract_selected.py
```

新增测试通过假的 CDP proxy 捕获 `_message_state()` 传入 `eval_script()` 的 JavaScript，并断言：

- 脚本中显式定义 `pageText`。
- 返回的 `page_text` 使用 `pageText.slice(-4000)`。
- 不再出现裸用未定义 `text.slice(-4000)`。
- `_message_state()` 能正常解析 proxy 返回的 JSON。

示例测试结构：

```python
def test_doubao_message_state_script_defines_page_text():
    class FakeProxy:
        script = ""

        async def eval_script(self, tab, script):
            self.script = script
            return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

    proxy = FakeProxy()
    extractor = DoubaoExtractor(proxy=proxy)
    state = asyncio.run(extractor._message_state(SimpleNamespace(tab_id="tab", url="https://www.doubao.com/chat/")))

    assert state["count"] == 0
    assert "const pageText = document.body?.innerText || '';" in proxy.script
    assert "page_text: pageText.slice(-4000)" in proxy.script
    assert "page_text: text.slice(-4000)" not in proxy.script
```

该测试会在修复前失败，因为当前脚本仍包含 `page_text: text.slice(-4000)`。

### 3. 验证真实写入链路

修复后需要验证：

1. `_message_state()` 不再因为 eval 抛 `Uncaught`。
2. `_send_prompt()` 可以进入写入逻辑。
3. prompt 和目标 URL 能出现在豆包输入框或新用户消息中。
4. 发送按钮被点击后，如果页面没有接收消息，会返回明确的 `doubao_send_not_confirmed`，不会误写 RawSource。
5. 豆包返回内容后，`/api/doubao/extract-selected` 才创建 RawSource。

## 数据流

```text
用户勾选抖音/B站条目
  -> /api/sync/prepare-selected
  -> /api/doubao/extract-selected
  -> DoubaoExtractor.extract_content(url)
  -> _ensure_tab() 打开/复用豆包页
  -> _message_state() 获取发送前消息数量
  -> _send_prompt() 写入 prompt + URL
  -> 点击发送按钮并确认消息已发送
  -> _wait_for_response_complete() 等待豆包回复稳定
  -> 返回内容
  -> RawSourceService.create_from_candidate()
  -> Candidate metadata 标记 doubao_extracted=True
```

本次根因位于 `_message_state()`，它发生在 `_send_prompt()` 写入输入框之前，因此会导致后续写入、点击、等待和入库全部没有发生。

## 边界条件与异常处理

- 豆包未登录：继续返回 `doubao_login_required`，不写 RawSource。
- 豆包输入框不存在：继续返回 `chat_input_not_ready`，不写 RawSource。
- prompt 写入失败：继续返回 `prompt_input_not_applied`，不写 RawSource。
- 发送按钮未找到或禁用：继续返回 `send_button_not_found` 或 `send_button_disabled`，不写 RawSource。
- 点击后未确认发送：继续返回 `doubao_send_not_confirmed`，不写 RawSource。
- 单条失败不阻断后续条目，失败 metadata 继续写入 `doubao_error`、`doubao_prompt`、`doubao_elapsed_seconds`。

## 预期结果

- “仅提取我勾选的内容”不会在写入 prompt 前因 `_message_state()` 抛错中断。
- 豆包输入框能收到完整 prompt 和目标链接。
- 发送按钮点击与发送确认逻辑能够继续执行。
- 失败时 metadata 中不再出现由未定义变量导致的 `eval failed: {"error":"Uncaught"}`。
- 成功时豆包回复内容会正常写入 RawSource，页面成功入库数量大于 0。
