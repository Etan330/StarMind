# 小红书点点重试、批处理完整性与原始资料标题展示修复总结

## 完成内容

- 点点无效短答复识别与最多一次重试
  - 新增对「暂时还没有好的思路」「换个问题试试」「暂时无法回答」等短答复的识别。
  - 同一条小红书内容第一次无效或空答复时，会重新发送同一分享文本和 prompt。
  - 第二次仍无效时返回失败，不写入 RawSource。
  - 返回结果包含 `attempts`、`retried`、`error`。

- 小红书点点批处理不中断
  - `/api/xiaohongshu/diandian/extract-selected` 按 candidate 独立处理。
  - 单条失败会记录失败 metadata 并继续后续条目。
  - 点点 tab 只在整个批次结束后关闭，不因单条失败提前关闭。
  - API response 每条 item 返回 `attempts`、`retried`、`error`。

- 原始资料标题规范化
  - `RawSourceService` 新增显示标题选择逻辑。
  - 标题优先级：有效 candidate/raw source 标题 → metadata 标题 → 小红书分享文本标题 → URL fallback。
  - 过滤完整 URL、note id、带 query 的 note id 片段等不适合作为标题的值。
  - 新建 RawSource 时，`RawSource.title`、`transcript.md` 一级标题、`raw.md` 一级标题均使用规范化标题。
  - `/ui/sources` 页面展示时会对历史 RawSource 重新计算显示标题，并修正详情区 transcript heading，避免已有旧数据继续显示 URL/URL 片段。

## 修改文件

- `app/connectors/xiaohongshu_diandian_extractor.py`
  - 增加无效点点答复识别。
  - 增加 `attempts`、`retried` 返回字段。
  - 实现最多 2 次发送逻辑。

- `app/api/routes.py`
  - 小红书点点 selected extraction 支持逐条失败不中断。
  - API response 和 candidate metadata 记录 retry/error 信息。
  - `/ui/sources` 使用规范化显示标题兼容历史 RawSource。

- `app/services/raw_source_service.py`
  - 增加标题规范化、分享文本标题解析、坏标题过滤、历史 source 展示标题计算。
  - transcript/raw text 生成使用规范化标题。

- `tests/test_xiaohongshu_diandian_extractor.py`
  - 覆盖第一次无效答复后重试成功、两次无效后失败。

- `tests/test_xiaohongshu_diandian_extract_selected.py`
  - 覆盖 4 条 selected candidate 均被处理。
  - 覆盖单条失败不阻断后续条目。

- `tests/test_raw_source_service_titles.py`
  - 覆盖 URL、note id query 片段、B站有效标题等标题展示场景。

## 验证结果

- Python 编译检查：
  - `PYTHONPATH=. .venv311/bin/python -m py_compile app/services/raw_source_service.py app/api/routes.py`
  - 结果：exit 0。

- RawSource 标题单测：
  - `PYTHONPATH=. .venv311/bin/pytest tests/test_raw_source_service_titles.py -v`
  - 结果：5 passed。

- 回归测试：
  - `PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_doubao_extract_selected.py tests/test_favorite_title_extractors.py tests/test_raw_source_service_titles.py -v`
  - 结果：51 passed, 1 warning。

- 浏览器 CDP 验证 `/ui/sources`：
  - 重启 8000 端口 uvicorn 后，打开 `http://127.0.0.1:8000/ui/sources`。
  - 收藏夹列表显示：
    - `20分钟AI做微信小程序｜保姆级全流程✅`
    - `发现了AI设计的新大陆：SVG！（可编辑）`
    - `目前主流企业AI Agent 技术栈选型`
    - `一次函数图像变换-旋转`
  - `rawLikeTitles` 为空。
  - 详情 `<h2>` 为 `20分钟AI做微信小程序｜保姆级全流程✅`。
  - transcript 一级标题为 `20分钟AI做微信小程序｜保姆级全流程✅`。

## 备注

- 现有本地历史 RawSource 数据库字段本身未做批量迁移；页面展示层会用 metadata 中的小红书分享文本标题实时修正，避免继续向用户展示 URL/URL 片段。
- 新产生的 RawSource 会在创建时写入规范化标题。