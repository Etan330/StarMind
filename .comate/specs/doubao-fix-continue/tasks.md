# StarMind 新版本同步收藏夹续跑、豆包发送闭环与平台卡片顺序修复任务计划

- [x] Task 1: 修复 `/ui/sync` 平台卡片顺序与图标断言
    - 1.1: 在 `StarMind/app/api/routes.py` 中定位 `PLATFORM_PRESETS` 与 `favorite_platform_cards()`
    - 1.2: 增加 `/ui/sync` 专用排序逻辑，使 `douyin` 固定第一、`tiktok` 固定最后
    - 1.3: 保持其它平台按原 `priority` 相对顺序展示
    - 1.4: 保持 `douyin.logo_url` 白底可见，保持 `tiktok.logo_url` 为 TikTok 自身图标
    - 1.5: 更新 `StarMind/tests/test_sync_favorites_page.py`，断言抖音第一、TikTok 最后、图标没有互相误改

- [x] Task 2: 增加同步收藏夹前端续跑状态存储
    - 2.1: 在 `StarMind/app/static/app.js` 的 `[data-batch-title-filter]` 初始化块中增加平台级 localStorage key
    - 2.2: 仅对 `douyin`、`xiaohongshu`、`bilibili` 启用续跑状态
    - 2.3: 增加安全读取、写入、清理 localStorage 的 helper
    - 2.4: 扫描成功后保存 `stage=scanned`、`scannedItems`、`summaryText`、`statusText`
    - 2.5: 分类成功后保存 `stage=classified`、`classifiedItems`、`groups`、`summaryText`、`statusText`
    - 2.6: prepare-selected 成功后保存 `stage=prepared`、`selectedCandidateIds` 与当前分类结果
    - 2.7: 提取完成后保存 `stage=completed` 与完成摘要，避免误导用户重复提取

- [x] Task 3: 增加同步收藏夹前端续跑状态恢复
    - 3.1: 抽取扫描预览渲染 helper，复用扫描成功与恢复逻辑
    - 3.2: 页面初始化时读取当前平台状态并校验 `version` 与 `platform`
    - 3.3: `stage=scanned` 时恢复扫描预览，启用 AI 分类，禁用提取
    - 3.4: `stage=classified` 时恢复分类分组和勾选框，启用提取
    - 3.5: `stage=prepared` 时恢复分类分组、候选 ID 和提取按钮
    - 3.6: `stage=completed` 时显示完成摘要，允许用户重新扫描开启新流程
    - 3.7: localStorage 数据损坏或结构不合法时清理状态并回到初始页面

- [x] Task 4: 修复豆包输入框写入与发送按钮点击确认
    - 4.1: 在 `StarMind/app/connectors/doubao_extractor.py` 中核对并补齐输入框 selector，覆盖 textarea、contenteditable、role textbox、ProseMirror 与 editor/input 容器
    - 4.2: 优先选择可见、可编辑、靠近 chat composer 的输入框
    - 4.3: 按 paste event、DOM setter、execCommand、textContent fallback 顺序写入 prompt
    - 4.4: 写入后必须确认输入框包含 prompt 头部或目标 URL，否则返回 `prompt_input_not_applied`
    - 4.5: 核对并补齐发送按钮候选范围到 button、role button、send/submit class、svg/use
    - 4.6: 按靠近输入框右侧、send/submit/arrow/plane 信号、尺寸与排除附件/上传按钮打分
    - 4.7: 先 DOM click，再用 `CDPProxy.click_at()` 真实坐标点击
    - 4.8: 点击后循环确认 prompt 已离开输入框或新消息包含 prompt/URL，否则返回 `doubao_send_not_confirmed`

- [x] Task 5: 修复豆包生成完成等待与单条失败记录
    - 5.1: 修改 `DoubaoExtractor.extract_content()`，调用 `_wait_for_response_complete()` 时传入 prompt
    - 5.2: 修改 `_wait_for_response_complete()`，忽略包含 prompt 头部或目标 URL 的用户消息
    - 5.3: 仅在 message count 增加、回复文本足够长、非生成中并连续稳定后返回内容
    - 5.4: 若最终文本仍是 prompt 或过短，返回空字符串
    - 5.5: 在 `/api/doubao/extract-selected` 单条失败时写入 `doubao_extracted=False`、`doubao_error`、`doubao_prompt`、`doubao_elapsed_seconds`
    - 5.6: 在 `/api/doubao/extract-selected` 单条成功时写入 `doubao_error=None`
    - 5.7: 保持单条失败不阻断后续 candidate 的批处理行为

- [x] Task 6: 补充回归测试
    - 6.1: 更新 `StarMind/tests/test_sync_favorites_page.py`，覆盖抖音第一、TikTok 最后和图标正确
    - 6.2: 更新 `StarMind/tests/test_doubao_extract_selected.py`，覆盖豆包单条失败会写入失败 metadata
    - 6.3: 更新 `StarMind/tests/test_doubao_extract_selected.py`，覆盖第一条失败不阻断后续条目
    - 6.4: 更新 `StarMind/tests/test_doubao_extract_selected.py`，覆盖成功 response 与 metadata 中 `doubao_error=None`
    - 6.5: 更新豆包 extractor 单元测试，验证 `_send_prompt` 脚本包含扩展 selector、真实点击坐标和发送确认逻辑
    - 6.6: 如项目无 JS 单测基础，则不新增前端测试框架，改用浏览器手动验证续跑 UI

- [x] Task 7: 运行自动化验证
    - 7.1: 在 `/Users/wyy/ ai/starmind/StarMind-main/StarMind` 执行 `PYTHONPATH=. python -m py_compile app/api/routes.py app/connectors/doubao_extractor.py`
    - 7.2: 执行 `PYTHONPATH=. pytest tests/test_sync_favorites_page.py tests/test_doubao_extract_selected.py -v`
    - 7.3: 必要时执行 `PYTHONPATH=. pytest tests/test_batch_title_filtering.py tests/test_favorite_scan_entrypoints.py tests/test_favorite_title_extractors.py -v`
    - 7.4: 修复验证中发现的与本次改动直接相关的问题

- [x] Task 8: 浏览器验证关键链路
    - 8.1: 重启或确认 8000 端口服务加载 `/Users/wyy/ ai/starmind/StarMind-main/StarMind` 目录代码
    - 8.2: 打开 `/ui/sync`，验证抖音第一、TikTok 最后、图标正确
    - 8.3: 打开 `/ui/source-setup/douyin`，扫描标题后刷新页面，验证扫描结果恢复
    - 8.4: 完成 AI 分类后刷新页面，验证分类界面恢复并可继续提取
    - 8.5: 必要时打开豆包页面，验证抖音/B站提取时 prompt 写入输入框、发送按钮被点击、生成完成后再入库
    - 8.6: 验证小红书仍使用点点链路，不走豆包

- [x] Task 9: 记录修复总结
    - 9.1: 确认 `StarMind/.comate/specs/doubao-fix-continue/tasks.md` 中任务复选框已更新
    - 9.2: 生成 `StarMind/.comate/specs/doubao-fix-continue/summary.md`
    - 9.3: 在 summary 中记录根因、修改文件、测试结果、浏览器验证结果与未覆盖风险
