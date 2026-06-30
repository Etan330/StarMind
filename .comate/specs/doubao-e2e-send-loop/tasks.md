# 豆包勾选提取端到端发送入库闭环修复任务计划

- [✓] Task 1: 补充真实豆包发送断点回归测试
    - 1.1: 在 `StarMind/tests/test_doubao_extract_selected.py` 中新增 fake proxy，模拟写入后按钮可见但发送后消息数不增加的场景
    - 1.2: 覆盖 `_send_prompt()` 必须在未确认发送时返回 `doubao_send_not_confirmed`
    - 1.3: 覆盖 `_send_prompt()` 返回给 Python 侧的诊断字段包含输入框内容、发送按钮坐标和页面消息数量
    - 1.4: 覆盖真实发送成功判定必须满足输入框清空或消息区新增 prompt/URL
    - 1.5: 先运行新增测试并确认当前代码不能满足新的端到端发送确认要求

- [✓] Task 2: 探查并封装 CDP 真实输入/按键能力
    - 2.1: 检查 `/Users/wyy/.claude/skills/web-access/scripts/cdp-proxy.mjs` 是否提供 keyboard、type、press、paste 或类似端点
    - 2.2: 如果已有端点，在 `StarMind/app/connectors/cdp_proxy.py` 增加最小 wrapper 方法
    - 2.3: 如果没有端点，不修改外部 proxy 脚本，改用页面内 focus、selection、beforeinput、input、keyup、execCommand 组合实现
    - 2.4: 保持已有 `click_at()` 行为兼容，不影响其它连接器

- [✓] Task 3: 重构豆包 `_send_prompt()` 为可验证的写入和发送两阶段
    - 3.1: 将输入框定位、内容写入、发送按钮定位、发送确认拆成清晰步骤
    - 3.2: 写入阶段返回并校验输入框内容必须包含 prompt 头部或目标 URL
    - 3.3: 发送阶段优先点击右下角真实发送按钮坐标
    - 3.4: 如坐标点击后未确认发送，再尝试 Enter 键发送
    - 3.5: 每次发送动作后轮询页面，确认输入框清空或消息区新增 prompt/URL
    - 3.6: 只有确认发送成功才返回 `success=True`
    - 3.7: 未确认发送时返回 `doubao_send_not_confirmed` 并附带诊断字段，避免误进入等待生成

- [✓] Task 4: 修复等待豆包生成并提取回复内容的闭环
    - 4.1: 确认 `extract_content()` 仅在 `_send_prompt()` 成功后调用 `_wait_for_response_complete()`
    - 4.2: 确认 `_wait_for_response_complete()` 忽略用户 prompt，自只返回豆包回复
    - 4.3: 确认回复稳定后返回非空文本，否则返回超时错误
    - 4.4: 确认 `/api/doubao/extract-selected` 成功后写入 RawSource 并设置 `doubao_extracted=True`、`doubao_error=None`
    - 4.5: 确认单条失败时记录失败 metadata 且不阻断后续勾选条目

- [✓] Task 5: 运行自动化验证
    - 5.1: 在 `/Users/wyy/ ai/starmind/StarMind-main/StarMind` 执行 `PYTHONPATH=. ../.venv311/bin/python -m pytest tests/test_doubao_extract_selected.py -v`
    - 5.2: 执行 `PYTHONPATH=. ../.venv311/bin/python -m py_compile app/connectors/doubao_extractor.py app/connectors/cdp_proxy.py tests/test_doubao_extract_selected.py`
    - 5.3: 如测试失败，仅修复与豆包端到端发送闭环直接相关的问题

- [✓] Task 6: 使用 Web Access 验证真实豆包写入和发送
    - 6.1: 执行 `node "/Users/wyy/.claude/skills/web-access/scripts/check-deps.mjs"` 确认 CDP proxy ready
    - 6.2: 打开或复用真实 `https://www.doubao.com/chat/` tab
    - 6.3: 调用 `_send_prompt()` 的受控测试 prompt，确认 textarea 出现完整 prompt 和 URL
    - 6.4: 确认右下角发送箭头被真实点击或 Enter 发送触发
    - 6.5: 确认发送后 textarea 清空或消息区新增用户 prompt/URL
    - 6.6: 确认 `_message_state()` 消息数量增加，且不再停留在“只写入未发送”状态

- [ ] Task 7: 使用真实 selected extraction 验证入库闭环
    - 7.1: 准备一个受控候选条目或使用现有失败候选条目
    - 7.2: 调用 `/api/doubao/extract-selected` 执行单条提取
    - 7.3: Web Access 验证豆包真实收到该条 prompt 和链接
    - 7.4: 等待豆包生成完成后确认接口返回成功或明确失败原因
    - 7.5: 成功时查询数据库确认 RawSource 新增，candidate metadata 为 `doubao_extracted=True`、`doubao_error=None`
    - 7.6: 失败时确认失败原因不是未写入、未发送或旧的 `eval failed: {"error":"Uncaught"}`

- [ ] Task 8: 记录修复总结
    - 8.1: 更新本 `tasks.md` 中已完成任务复选框
    - 8.2: 生成 `StarMind/.comate/specs/doubao-e2e-send-loop/summary.md`
    - 8.3: 在 summary 中记录 Web Access 实测证据、根因、修改文件、测试命令、真实入库验证结果与剩余风险
