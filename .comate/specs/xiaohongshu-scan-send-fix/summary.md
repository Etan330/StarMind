# 小红书收藏扫描去重与点点发送可靠性修复总结

## 修复内容

- 修复小红书收藏页扫描重复：
  - `extension/xiaohongshu_eval.js` 明确限制 note id 提取路径，只接受 `/explore/{noteId}`、`/discovery/item/{noteId}`、`/user/profile/{userId}/{noteId}`。
  - eval 输出前按 `note_id` 合并，优先保留标题质量更高、分享文本完整的记录。
  - `app/connectors/xiaohongshu.py` 增加 Collector 层防御式去重，避免 eval 或页面 DOM 输出重复时进入 API 响应。
- 修复异常 UI 文本误入扫描：
  - 过滤 `[我`、过短孤立文本、导航/按钮/平台 UI 文案。
  - 对没有有效 URL 或明显噪声标题的条目跳过。
- 保持小红书分享文本要求：
  - 继续生成 `【标题 | 小红书 - 你的生活兴趣社区】 https://www.xiaohongshu.com/discovery/item/...`。
  - 保留 `xsec_token` 并将 `xsec_source` 转为 `pc_share`。
- 修复点点发送可靠性：
  - `XiaohongshuDiandianExtractor._send_prompt()` 在填入 prompt 后调用真实坐标点击。
  - 发送后增加确认轮询：输入框清空、消息数量增加或页面消息区域出现用户消息后才认为发送成功。
  - 未确认发送时返回 `xiaohongshu_diandian_send_not_confirmed`，不继续写 RawSource。
  - `_wait_for_response_complete()` 忽略用户刚发送的 prompt，只在点点回复稳定至少两轮后返回内容。
- 修正 CDP 点击接口使用：
  - `app/connectors/cdp_proxy.py` 当前环境已验证 `/clickXY` 支持坐标点击，因此优先使用 `/clickXY`，并保留 `/clickAt` fallback。

## 修改文件

- `extension/xiaohongshu_eval.js`
- `app/connectors/xiaohongshu.py`
- `app/connectors/xiaohongshu_diandian_extractor.py`
- `app/connectors/cdp_proxy.py`
- `tests/test_favorite_title_extractors.py`
- `tests/test_xiaohongshu_diandian_extractor.py`
- `.comate/specs/xiaohongshu-scan-send-fix/tasks.md`

## 验证结果

### 自动化测试

执行：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_favorite_title_extractors.py tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_favorite_scan_entrypoints.py tests/test_sync_favorites_page.py tests/test_doubao_extract_selected.py -v
```

结果：

```text
71 passed, 1 warning in 47.62s
```

### 浏览器/CDP 验证

- 已重启 `127.0.0.1:8000` 的 uvicorn 服务。
- 已在浏览器页 `http://127.0.0.1:8000/ui/source-setup/xiaohongshu` 通过 `/api/sync/scan-titles` 触发真实小红书收藏页扫描。
- 返回结果：
  - `total: 10`
  - `duplicates: []`
  - `hasBracketWo: false`
  - `Anthropic博客的Agent Eval实践心得` 只出现一次。
- 已验证浏览器加载的 `/static/app.js` 仍包含：
  - `/api/xiaohongshu/diandian/extract-selected`
  - `点点提取中`
  - `platform === "xiaohongshu"`
- 已验证当前 CDP proxy 的坐标点击接口：
  - `/clickXY` 返回 `200 {"clicked": true}`。
  - `/clickAt` 在当前 proxy 中按 selector 解释 body，不适合 JSON 坐标，因此代码中保留为 fallback。
- 已检查点点页面：
  - `https://www.xiaohongshu.com/ai_chat?...` 可访问。
  - 页面存在输入框。
  - 页面已有历史点点回复内容，说明登录态和点点页面可用。

## 未完整自动执行的项

- 未在真实点点页面主动发送新的生产 prompt，以避免向用户当前点点会话再次写入测试内容。
- 发送链路已通过单元测试覆盖：必须调用坐标点击、必须确认发送成功、必须等待点点回复稳定后才返回。
