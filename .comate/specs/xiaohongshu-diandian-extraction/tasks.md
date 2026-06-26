# 小红书收藏夹点点提取任务计划

- [✓] Task 1: 补齐小红书收藏扫描中的分享文本字段
    - 1.1: 在 `tests/test_favorite_title_extractors.py` 增加小红书 eval 脚本用例，覆盖 profile/收藏直链转 `discovery/item` 分享链接、保留 `xsec_token`、`xsec_source=pc_share`、`share_text` 包含标题
    - 1.2: 修改 `extension/xiaohongshu_eval.js`，从小红书笔记 URL 提取 `note_id` 和 `xsec_token`，构造 `share_url` 与 `share_text`
    - 1.3: 增加 Collector 用例，验证 `XiaohongshuFavoritesCollector` 将 `xiaohongshu_note_id`、`xiaohongshu_share_url`、`xiaohongshu_share_text` 写入 metadata
    - 1.4: 修改 `app/connectors/xiaohongshu.py`，把 eval 返回的分享字段保存在 ConnectorItem metadata，保持 raw_url 仍为原始页面 URL
    - 1.5: 运行 `pytest tests/test_favorite_title_extractors.py -v`，确认小红书标题与分享字段测试通过

- [✓] Task 2: 新增小红书点点网页提取器
    - 2.1: 新建 `app/connectors/xiaohongshu_diandian_extractor.py`，定义 `DIANDIAN_URL`、`XIAOHONGSHU_DIANDIAN_PROMPT`、`DiandianExtractResult`
    - 2.2: 实现 `_ensure_tab()`，优先复用已打开的 `xiaohongshu.com/ai_chat` 标签页，否则打开固定点点入口
    - 2.3: 实现 `check_ready()`，检测页面是否存在可见可输入的聊天输入框，并返回点点是否可用
    - 2.4: 实现 `extract_content()`、`_send_prompt()`、`_message_state()`、`_wait_for_response_complete()` 和 `close()`，按「分享文本 + prompt」逐条发送并读取稳定回复
    - 2.5: 新增 `tests/test_xiaohongshu_diandian_extractor.py`，用 FakeProxy 覆盖复用已有点点 tab、新开点点 tab、未就绪检测、prompt 包含分享文本、返回内容成功等行为
    - 2.6: 运行 `pytest tests/test_xiaohongshu_diandian_extractor.py -v`，确认提取器单元测试通过

- [✓] Task 3: 新增小红书点点 extract-selected 后端接口
    - 3.1: 新增 `tests/test_xiaohongshu_diandian_extract_selected.py`，构造小红书 CandidateItem，验证接口成功后创建 RawSource 并写入 `xiaohongshu_diandian_extracted` metadata
    - 3.2: 在测试中验证传给 fake 点点提取器的是 `xiaohongshu_share_text`，不是 profile/收藏直链 raw_url
    - 3.3: 在测试中覆盖旧候选无分享文本时的 fallback：从 raw_url/canonical_url 构造 `discovery/item` 分享链接并带标题
    - 3.4: 在测试中覆盖点点未就绪时返回错误码 `xiaohongshu_diandian_not_ready`
    - 3.5: 修改 `app/api/routes.py`，新增小红书分享文本构造辅助函数，优先使用 metadata 中的 `xiaohongshu_share_text` 和 `xiaohongshu_share_url`
    - 3.6: 修改 `app/api/routes.py`，新增 `POST /api/xiaohongshu/diandian/extract-selected`，复用 RawSourceService/WikiMaintenanceService 入库逻辑，失败时标记 ledger 为 `xiaohongshu_diandian_failed`
    - 3.7: 修改候选复用逻辑，使已存在 RawSource 或 metadata 中 `xiaohongshu_diandian_extracted=true` 的小红书候选不会被重复准备提取
    - 3.8: 运行 `pytest tests/test_xiaohongshu_diandian_extract_selected.py -v`，确认新接口测试通过

- [✓] Task 4: 前端按平台切换小红书点点提取流程
    - 4.1: 修改 `app/static/app.js`，在「仅提取我勾选的内容」点击流程中判断 `platform === "xiaohongshu"`
    - 4.2: 小红书平台调用 `/api/xiaohongshu/diandian/extract-selected`，状态文案改为「点点提取中」「发送到小红书点点」
    - 4.3: 非小红书平台继续调用 `/api/doubao/extract-selected`，保持现有豆包文案与错误处理
    - 4.4: 增加小红书点点错误码处理，遇到 `xiaohongshu_diandian_not_ready` 时提示确认浏览器仍登录小红书并打开点点入口检查
    - 4.5: 修改 `app/templates/source_setup.html`，小红书前置条件显示「浏览器已登录小红书，点点入口可用」，B站/抖音仍显示豆包登录前置条件

- [✓] Task 5: 回归现有豆包与 prepare-selected 行为
    - 5.1: 运行 `pytest tests/test_doubao_extract_selected.py -v`，确认抖音/B站等非小红书仍走豆包流程
    - 5.2: 如果测试暴露 `candidate_ids_for_items()` 与新 metadata 字段冲突，只调整重复提取判断，不改变现有 DoubaoExtractor 行为
    - 5.3: 运行 `pytest tests/test_favorite_scan_entrypoints.py tests/test_sync_favorites_page.py -v`，确认收藏夹入口和页面渲染未被破坏

- [✓] Task 6: 全量验证与结果整理
    - 6.1: 运行 `pytest tests/test_favorite_title_extractors.py tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_doubao_extract_selected.py tests/test_favorite_scan_entrypoints.py tests/test_sync_favorites_page.py -v`
    - 6.2: 检查关键代码路径，确认小红书发送给点点的 payload 优先使用带标题的分享文本/分享链接，不使用 profile 收藏直链
    - 6.3: 更新本任务文件中所有已完成任务复选框
    - 6.4: 生成 `summary.md`，记录完成内容、测试结果、未覆盖的真实浏览器手动验证项
