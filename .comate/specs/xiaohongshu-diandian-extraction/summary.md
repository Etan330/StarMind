# 小红书收藏夹点点提取完成总结

## 完成内容

- 已将小红书点点提取实现同步到主工作区 `/Users/wyy/ ai/starmind/StarMind-main`。
- 小红书收藏标题扫描新增分享字段：
  - `note_id`
  - `xsec_token`
  - `share_url`
  - `share_text`
- 小红书分享链接会从 profile/收藏直链转换为 `https://www.xiaohongshu.com/discovery/item/{note_id}?source=webshare&xhsshare=pc_web&...&xsec_source=pc_share`。
- `XiaohongshuFavoritesCollector` 会把分享字段写入 Candidate metadata，保留 raw_url 作为原始来源 URL。
- 新增 `XiaohongshuDiandianExtractor`：
  - 打开或复用 `https://www.xiaohongshu.com/ai_chat`
  - 检测点点输入框是否可用
  - 逐条发送「小红书分享文本 + Prompt」
  - 等待回复稳定并返回提取内容
- 新增 `/api/xiaohongshu/diandian/extract-selected`：
  - 对小红书候选逐条调用点点
  - 成功后写入 RawSource
  - metadata 写入 `xiaohongshu_diandian_extracted`、prompt、share_text、share_url、耗时和响应长度
  - 成功后 ledger 标记为 `knowledge`
  - 失败条目标记 `xiaohongshu_diandian_failed`
- 前端「仅提取我勾选的内容」按平台分流：
  - 小红书调用点点接口并显示点点文案
  - 其他平台继续调用豆包接口
- 小红书设置页已补齐 UI 文案分流：
  - 标题显示「先筛掉噪声，再让点点深读」
  - 流程步骤显示「点点提取入库」
  - 前置条件显示「浏览器已登录小红书，点点入口可用」
  - 高级直接提取按钮显示「直接采集 → 点点提取 → 写入知识库」
- 已重启本地 `127.0.0.1:8000` 上的 uvicorn 服务，使主工作区代码生效。

## 根因修复记录

- 用户反馈页面仍跳豆包且 UI 仍是豆包文案后，定位到根因是：此前实现和验证在隔离 worktree 中完成，但用户访问的本地服务运行在主工作区 `main`，主工作区未完整同步改动且服务未重启。
- 已把点点提取相关代码、测试和 UI 文案同步到主工作区，并重启端口 8000 的本地服务。

## 验证结果

在主工作区使用 Python 3.11 虚拟环境执行：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_favorite_title_extractors.py tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_doubao_extract_selected.py tests/test_favorite_scan_entrypoints.py tests/test_sync_favorites_page.py -v
```

结果：

```text
65 passed, 1 warning in 34.24s
```

浏览器验证：

- 已通过 CDP 刷新 `http://127.0.0.1:8000/ui/source-setup/xiaohongshu`。
- 页面标题为 `小红书 管理`。
- 页面正文已包含「点点」。
- 页面正文不再包含小红书流程中的旧豆包提取文案：
  - `先筛掉噪声，再让豆包深读`
  - `豆包提取入库`
  - `直接采集 → 豆包提取`
  - `浏览器已登录豆包`
- 浏览器加载的 `/static/app.js` 已包含：
  - `/api/xiaohongshu/diandian/extract-selected`
  - `点点提取中`
  - `platform === "xiaohongshu"`

## 未覆盖项

- 尚未实际点击完整真实链路：扫描标题、AI 分类、勾选条目、发送到真实小红书点点并等待真实回复入库。
- 点点页面 DOM 选择器已做通用适配，但真实页面若改版，可能需要微调输入框/发送按钮/消息节点 selector。
