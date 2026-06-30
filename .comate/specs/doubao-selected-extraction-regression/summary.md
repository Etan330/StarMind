# 豆包勾选提取未写入 Prompt 回归修复总结

## 修复范围

本次只修改新版本目录：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind
```

## 根因

用户勾选抖音/B站内容后，豆包页面会打开，但 prompt 和链接没有进入对话框。根因在 `DoubaoExtractor._send_prompt()` 写入输入框前调用的 `_message_state()`。

`_message_state()` 注入页面的 JavaScript 返回 `page_text` 时使用了未定义变量：

```javascript
page_text: text.slice(-4000)
```

这会触发浏览器 eval 抛错：

```text
eval failed: {"error":"Uncaught"}
```

因此 `_send_prompt()` 在真正写入 prompt 前就中断，后续发送按钮点击、等待生成和 RawSource 入库都不会发生。

## 修改文件

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind/app/connectors/doubao_extractor.py
/Users/wyy/ ai/starmind/StarMind-main/StarMind/tests/test_doubao_extract_selected.py
/Users/wyy/ ai/starmind/StarMind-main/StarMind/.comate/specs/doubao-selected-extraction-regression/doc.md
/Users/wyy/ ai/starmind/StarMind-main/StarMind/.comate/specs/doubao-selected-extraction-regression/tasks.md
/Users/wyy/ ai/starmind/StarMind-main/StarMind/.comate/specs/doubao-selected-extraction-regression/summary.md
```

## 代码修复

在 `app/connectors/doubao_extractor.py` 的 `_message_state()` 中新增页面全文变量：

```javascript
const pageText = document.body?.innerText || '';
```

并将返回值改为：

```javascript
page_text: pageText.slice(-4000)
```

保留原来的 `count`、`text`、`generating` 语义，不改动无关的输入框 selector、发送按钮打分和 RawSource 入库逻辑。

## 回归测试

在 `tests/test_doubao_extract_selected.py` 新增测试：

```text
test_doubao_message_state_script_defines_page_text
```

该测试先捕获 `_message_state()` 传入 CDP proxy 的 JavaScript，并断言：

- 脚本定义 `const pageText = document.body?.innerText || '';`
- 返回值使用 `page_text: pageText.slice(-4000)`
- 不再包含 `page_text: text.slice(-4000)`

修复前该测试已确认失败；修复后通过。

## 验证结果

自动化测试：

```text
PYTHONPATH=. ../.venv311/bin/python -m pytest tests/test_doubao_extract_selected.py -v
```

结果：

```text
19 passed, 1 warning
```

语法检查：

```text
PYTHONPATH=. ../.venv311/bin/python -m py_compile app/connectors/doubao_extractor.py tests/test_doubao_extract_selected.py
```

结果：通过，无输出。

真实豆包入口验证：

1. `node "/Users/wyy/.claude/skills/web-access/scripts/check-deps.mjs"` 通过，CDP proxy ready。
2. 调用真实豆包 tab 的 `_message_state()` 成功返回：

```json
{"count": 0, "generating": false, "page_text_len": 516}
```

不再出现 `eval failed: {"error":"Uncaught"}`。

3. 调用 `_send_prompt()` 的受控测试 prompt 后，豆包输入框中已出现测试 prompt 和 URL：

```text
StarMind 回归验证：请不要发送真实提取，仅确认输入写入 https://example.com/starmind-doubao-regression
```

本次验证中 `_send_prompt()` 返回：

```json
{"success": false, "error": "doubao_send_not_confirmed"}
```

这说明当前根因已修复：流程已经越过 `_message_state()` 并完成输入框写入。发送确认仍按现有保护逻辑执行；如果页面没有确认消息发送，会返回明确错误码，不会误写 RawSource。

## 剩余风险

- 本次修复解决的是“写入前 eval 抛错导致完全不写 prompt”的根因。
- 真实豆包页面发送按钮确认仍可能受页面状态、按钮禁用、账号状态或豆包前端变更影响；当前保护逻辑会返回 `doubao_send_not_confirmed`，避免误入库。
- 若后续用户再次遇到“已写入但没有发送”的问题，应继续针对发送按钮点击和发送确认逻辑单独排查。
