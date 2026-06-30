# 豆包勾选提取未写入 Prompt 回归修复任务计划

- [x] Task 1: 添加 `_message_state()` 未定义变量回归测试
    - 1.1: 在 `StarMind/tests/test_doubao_extract_selected.py` 中新增 fake CDP proxy 捕获 `_message_state()` 注入脚本
    - 1.2: 调用 `DoubaoExtractor._message_state()` 并确认能解析 proxy 返回的消息状态
    - 1.3: 断言脚本定义 `pageText` 并使用 `pageText.slice(-4000)` 返回 `page_text`
    - 1.4: 断言脚本不再包含会触发 `Uncaught` 的 `page_text: text.slice(-4000)`
    - 1.5: 先运行该测试并确认当前代码因未定义变量脚本断言失败

- [x] Task 2: 修复 `_message_state()` 页面状态脚本
    - 2.1: 修改 `StarMind/app/connectors/doubao_extractor.py` 中 `DoubaoExtractor._message_state()`
    - 2.2: 在注入脚本内定义 `const pageText = document.body?.innerText || '';`
    - 2.3: 将返回值中的 `page_text` 改为 `pageText.slice(-4000)`
    - 2.4: 保持 `count`、`text`、`generating` 字段语义不变
    - 2.5: 不改动无关的输入框 selector、发送按钮打分和 RawSource 入库逻辑

- [x] Task 3: 验证自动化测试与语法检查
    - 3.1: 在 `/Users/wyy/ ai/starmind/StarMind-main/StarMind` 执行 `PYTHONPATH=. ../.venv311/bin/python -m pytest tests/test_doubao_extract_selected.py -v`
    - 3.2: 执行 `PYTHONPATH=. ../.venv311/bin/python -m py_compile app/connectors/doubao_extractor.py tests/test_doubao_extract_selected.py`
    - 3.3: 如测试失败，仅修复与 `_message_state()` 回归直接相关的问题

- [x] Task 4: 验证真实豆包写入入口
    - 4.1: 通过当前 CDP 豆包 tab 或新建豆包 tab 调用 `_message_state()`，确认不再返回 `eval failed: {"error":"Uncaught"}`
    - 4.2: 调用 `_send_prompt()` 的受控测试 prompt，确认流程至少能越过 `_message_state()` 并进入输入框写入逻辑
    - 4.3: 检查豆包输入框或页面消息中是否出现测试 prompt / URL
    - 4.4: 若发送未完成，记录新的明确错误码，不再出现未定义变量导致的 eval failed

- [x] Task 5: 记录修复总结
    - 5.1: 更新本 `tasks.md` 中已完成任务复选框
    - 5.2: 生成 `StarMind/.comate/specs/doubao-selected-extraction-regression/summary.md`
    - 5.3: 在 summary 中记录根因、修改文件、测试命令、真实豆包验证结果与剩余风险
