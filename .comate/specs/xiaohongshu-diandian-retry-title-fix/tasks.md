# 小红书点点重试、批处理完整性与原始资料标题展示修复任务计划

- [x] Task 1: 补充点点无效答复重试失败测试
    - 1.1: 在 `tests/test_xiaohongshu_diandian_extractor.py` 增加第一次返回「这个问题我暂时还没有好的思路，换个问题试试吧」、第二次返回有效内容时应成功的测试
    - 1.2: 断言同一条分享文本和 prompt 会发送两次，返回结果 `attempts=2`、`retried=True`
    - 1.3: 增加两次都返回无效短答复时应失败的测试，断言错误码为 `xiaohongshu_diandian_unhelpful_response`
    - 1.4: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py -v`，确认新增测试暴露当前无重试问题

- [x] Task 2: 实现点点无效答复识别与最多一次重试
    - 2.1: 在 `app/connectors/xiaohongshu_diandian_extractor.py` 新增 `is_unhelpful_diandian_response()`，识别「暂时还没有好的思路」「换个问题试试」「暂时无法回答」等无效短答复
    - 2.2: 扩展 `DiandianExtractResult`，增加 `attempts` 和 `retried` 字段
    - 2.3: 修改 `extract_content()`，对同一条内容最多尝试 2 次，第一次空答复或无效答复时重新发送同一 prompt
    - 2.4: 第二次仍无效时返回失败，不写 RawSource，错误码为 `xiaohongshu_diandian_unhelpful_response`
    - 2.5: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py -v`，确认点点重试测试通过

- [x] Task 3: 补充小红书点点批处理完整性测试
    - 3.1: 在 `tests/test_xiaohongshu_diandian_extract_selected.py` 增加 4 个 candidate 的批处理测试
    - 3.2: 模拟第 1 条第一次无效、第二次成功，第 2/3/4 条直接成功，断言 4 条都被调用并入库
    - 3.3: 增加单条失败不阻断后续 candidate 的测试，断言后续条目仍被发送和记录
    - 3.4: 断言 API 响应 items 中包含每条的 `attempts`、`retried`、`error`
    - 3.5: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extract_selected.py -v`，确认当前批处理记录不完整问题暴露

- [x] Task 4: 修复后端点点批处理结果记录与不中断行为
    - 4.1: 修改 `app/api/routes.py` 的 `/api/xiaohongshu/diandian/extract-selected`，确保每个 candidate 单独 try/except，单条失败只记录失败并继续下一条
    - 4.2: 写入 candidate metadata：`xiaohongshu_diandian_attempts`、`xiaohongshu_diandian_retried`、`xiaohongshu_diandian_error`
    - 4.3: API response 每条 item 返回 attempts、retried、error，方便前端确认失败原因
    - 4.4: 确保点点 tab 只在整个批次结束后关闭，不因单条失败提前关闭
    - 4.5: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extract_selected.py -v`，确认 4 条批处理测试通过

- [x] Task 5: 补充 RawSource 标题规范化测试
    - 5.1: 新增或扩展 RawSourceService 测试：candidate.title 是 URL，但 `xiaohongshu_share_text` 有标题时，RawSource.title 使用分享文本标题
    - 5.2: 断言 transcript 一级标题 `# ...` 使用真实标题，不使用 URL
    - 5.3: 断言 raw.md 一级标题 `# 原始资料：...` 使用真实标题
    - 5.4: 增加 B站/抖音 candidate 本身有标题时保持标题、不被 URL 覆盖的测试
    - 5.5: 运行对应测试，确认当前标题 fallback 问题暴露

- [x] Task 6: 实现 RawSource 标题规范化
    - 6.1: 修改 `app/services/raw_source_service.py`，新增 `_display_title(candidate, metadata)`
    - 6.2: 标题优先级按 doc.md 执行：有效 candidate.title → metadata title/yt_dlp_title/xiaohongshu_title → 小红书分享文本提取标题 → fallback
    - 6.3: 将规范化标题用于 `RawSource.title`
    - 6.4: 将规范化标题传入 `_build_transcript()` 和 `_build_raw_text()`，使详情内容一级标题也显示真实标题
    - 6.5: 运行 RawSourceService 相关测试，确认标题展示测试通过

- [x] Task 7: 回归测试与浏览器页面验证
    - 7.1: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_doubao_extract_selected.py tests/test_favorite_title_extractors.py -v`
    - 7.2: 如本地服务仍运行旧代码，重启 `127.0.0.1:8000` uvicorn
    - 7.3: 用浏览器 CDP 打开 `/ui/sources`，验证收藏夹列表显示真实标题而不是 URL
    - 7.4: 点击一条 RawSource，验证详情 `<h2>` 和 transcript 一级标题显示真实标题
    - 7.5: 检查点点提取 API 对 4 条候选的响应结构包含每条 attempts/retried/error

- [x] Task 8: 记录修复结果
    - 8.1: 更新 `.comate/specs/xiaohongshu-diandian-retry-title-fix/tasks.md` 中已完成任务复选框
    - 8.2: 生成 `.comate/specs/xiaohongshu-diandian-retry-title-fix/summary.md`，记录根因、修改文件、测试结果和浏览器验证结果
