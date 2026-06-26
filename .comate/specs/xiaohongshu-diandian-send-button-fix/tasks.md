# 小红书点点发送按钮误点加号修复任务计划

- [x] Task 1: 补充点点发送按钮选择失败测试
    - 1.1: 在 `tests/test_xiaohongshu_diandian_extractor.py` 构造真实点点输入框 DOM：左下角 `button.ai-input-action-btn` 使用 `#addM`，右下角 `div.submit-button-wrapper` 使用 `#arrow_top`
    - 1.2: 增加测试断言 `_send_prompt()` 返回并点击的坐标必须落在右下角发送箭头区域
    - 1.3: 增加测试断言 `_send_prompt()` 不允许选择左下角加号坐标
    - 1.4: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py -v`，确认旧逻辑无法通过新增测试

- [x] Task 2: 修复点点发送按钮候选节点范围
    - 2.1: 修改 `app/connectors/xiaohongshu_diandian_extractor.py` 中 `_send_prompt()` 的 JS 候选选择器
    - 2.2: 将候选节点从 `button, [role="button"]` 扩展到 `.submit-button-wrapper`、`.submit-button`、`.bottom-box-right`、`.bottom-box-right-submit-button`、`svg.submit-button`
    - 2.3: 在候选特征中读取 `use[href]` 和 `use[xlink:href]`，识别 `#arrow_top` 与 `#addM`
    - 2.4: 保留输入框填充和发送后确认逻辑不变

- [x] Task 3: 修复发送按钮评分与排除规则
    - 3.1: 对 `#arrow_top`、`.submit-button-wrapper`、位于输入框右下方的候选加高分
    - 3.2: 对 `#addM`、`.ai-input-action-btn`、`.bottom-box-left`、位于输入框左下方的候选大幅扣分
    - 3.3: 如果没有找到 `#arrow_top` 或右下方提交候选，则返回 `send_button_not_found`，不点击加号兜底
    - 3.4: 点击坐标取最终目标容器中心，优先使用 `.submit-button-wrapper` 外层容器

- [x] Task 4: 验证点点发送按钮选择逻辑
    - 4.1: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py -v`
    - 4.2: 用浏览器 CDP 检查真实点点页面，确认左下角加号坐标约为 `x≈419`、右下角箭头坐标约为 `x≈1177`
    - 4.3: 在真实点点页面运行选择逻辑但不发送，确认返回的候选为 `#arrow_top` / `.submit-button-wrapper`
    - 4.4: 确认新逻辑不会选择 `#addM` / `.ai-input-action-btn`

- [x] Task 5: 回归服务与记录结果
    - 5.1: 运行相关回归测试，至少覆盖点点提取、小红书点点接口、豆包点击发送相关测试
    - 5.2: 如本地 `127.0.0.1:8000` 服务仍运行旧代码，重启 uvicorn
    - 5.3: 更新 `.comate/specs/xiaohongshu-diandian-send-button-fix/tasks.md` 复选框
    - 5.4: 生成 `.comate/specs/xiaohongshu-diandian-send-button-fix/summary.md`，记录根因、修复内容、测试结果和浏览器验证证据
