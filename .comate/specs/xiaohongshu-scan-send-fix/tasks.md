# 小红书收藏扫描去重与点点发送可靠性修复任务计划

- [x] Task 1: 补充小红书扫描重复与异常条目的失败测试
    - 1.1: 在 `tests/test_favorite_title_extractors.py` 增加同一 note 同时出现 profile 收藏链接和 discovery 分享链接时只输出一条的测试
    - 1.2: 在 `tests/test_favorite_title_extractors.py` 增加同一卡片多个 anchor 指向同一 note 时只输出一条的测试
    - 1.3: 在 `tests/test_favorite_title_extractors.py` 增加 `[我`、纯 UI 短文本、页面导航文本不会作为收藏标题输出的测试
    - 1.4: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_favorite_title_extractors.py -v`，确认新增测试能暴露当前问题

- [x] Task 2: 修复小红书 DOM 扫描过滤与去重
    - 2.1: 修改 `extension/xiaohongshu_eval.js`，把 note id 提取限制到 `/explore/{noteId}`、`/discovery/item/{noteId}`、`/user/profile/{userId}/{noteId}` 等明确笔记路径
    - 2.2: 修改 `extension/xiaohongshu_eval.js`，收窄候选容器优先级，优先使用笔记卡片容器，减少全页 UI 链接误入
    - 2.3: 修改 `extension/xiaohongshu_eval.js`，增强 `isBadTitle()`，过滤 `[我`、过短异常文本、按钮/导航/平台 UI 文案
    - 2.4: 修改 `extension/xiaohongshu_eval.js`，输出前按 `note_id` 二次合并，优先保留标题分数高、标题非空、分享文本完整的记录
    - 2.5: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_favorite_title_extractors.py -v`，确认扫描脚本测试通过

- [x] Task 3: 在 Collector 层增加防御式去重
    - 3.1: 修改 `app/connectors/xiaohongshu.py`，在 `extract_favorites()` 中按 `note_id`、分享 URL 中的 note id、去 query URL 生成唯一 key
    - 3.2: 跳过已出现过的 key，避免 eval 脚本或页面 DOM 输出重复项时进入 API 响应
    - 3.3: 对没有有效 URL/note id 且标题异常的条目跳过，不把页面 UI 噪声作为收藏返回
    - 3.4: 增加或扩展 Collector 单元测试，验证 Collector 会去重并过滤异常扫描项
    - 3.5: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_favorite_title_extractors.py -v`，确认 Collector 测试通过

- [x] Task 4: 补充点点发送确认的失败测试
    - 4.1: 在 `tests/test_xiaohongshu_diandian_extractor.py` 增加 `_send_prompt()` 成功路径必须调用 `click_at()` 的测试
    - 4.2: 增加发送按钮被点击但消息数量未增加、输入框未清空时返回 `xiaohongshu_diandian_send_not_confirmed` 的测试
    - 4.3: 增加等待回复时忽略用户刚发送 prompt、只在点点回复稳定后返回内容的测试
    - 4.4: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py -v`，确认新增测试能暴露当前问题

- [x] Task 5: 修复 CDP 真实点击与点点发送等待逻辑
    - 5.1: 修改 `app/connectors/cdp_proxy.py`，将真实坐标点击优先改为 web-access proxy 的 `/clickAt` 接口，并保留旧 `/clickXY` fallback
    - 5.2: 修改 `app/connectors/xiaohongshu_diandian_extractor.py`，填入 prompt 后优先使用 `click_at()` 点击发送按钮中心坐标
    - 5.3: 修改 `_send_prompt()`，发送后轮询验证输入框清空、消息数量增加或页面出现用户消息；未确认则返回明确错误
    - 5.4: 修改 `_wait_for_response_complete()`，忽略用户 prompt 消息，等待点点回复内容稳定至少两轮再返回
    - 5.5: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py -v`，确认点点提取测试通过

- [x] Task 6: 回归小红书 API、页面和非小红书流程
    - 6.1: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_favorite_title_extractors.py tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_favorite_scan_entrypoints.py tests/test_sync_favorites_page.py -v`
    - 6.2: 回归 `tests/test_doubao_extract_selected.py`，确认豆包提取逻辑未被 CDP 点击调整破坏
    - 6.3: 用浏览器 CDP 刷新 `http://127.0.0.1:8000/ui/source-setup/xiaohongshu` 并触发扫描，检查结果中不再有重复标题和 `[我` 异常条目
    - 6.4: 用浏览器 CDP 观察点点页面发送链路，确认 prompt 被发送、页面进入生成状态并等待回复，而不是只停留在输入框中
    - 6.5: 若本地服务仍加载旧代码，重启 `127.0.0.1:8000` uvicorn 后重新验证

- [x] Task 7: 记录修复结果
    - 7.1: 更新 `.comate/specs/xiaohongshu-scan-send-fix/tasks.md` 中已完成任务复选框
    - 7.2: 生成 `.comate/specs/xiaohongshu-scan-send-fix/summary.md`，记录根因、修改文件、测试结果和浏览器验证结果
