# StarMind 新版本同步收藏夹续跑与豆包发送闭环修复总结

## 执行范围

本次只在新版本目录执行：

```text
/Users/wyy/ ai/starmind/StarMind-main/StarMind
```

外层旧版本目录未作为迭代目标。

## 已完成任务

- 修复 `/ui/sync` 平台卡片排序：抖音固定第一，TikTok 固定最后，其它平台保持原 priority 相对顺序。
- 增加抖音、小红书、B站前端续跑状态保存：扫描、分类、准备提取、提取完成阶段均写入 localStorage。
- 增加前端续跑状态恢复：页面刷新/重新进入后可恢复扫描预览、分类分组、候选 ID 和完成摘要。
- 修复小红书 selected extraction 前端入口：小红书继续调用点点接口，抖音/B站调用豆包接口。
- 增强豆包发送闭环：扩展输入框/发送按钮 selector，增加输入框写入验证、发送确认和真实坐标点击。
- 增强豆包生成等待：等待函数接收 prompt，排除用户 prompt 本身，避免误把 prompt 当成豆包回复。
- 增强 selected extraction metadata：成功写入 `doubao_error=None`，失败写入 `doubao_extracted=False`、`doubao_error`、`doubao_prompt`、`doubao_elapsed_seconds`，并保持单条失败不阻断后续条目。
- 补充后端与浏览器验证。

## 修改文件

```text
app/api/routes.py
app/static/app.js
app/connectors/doubao_extractor.py
tests/test_sync_favorites_page.py
tests/test_doubao_extract_selected.py
.comate/specs/doubao-fix-continue/doc.md
.comate/specs/doubao-fix-continue/tasks.md
.comate/specs/doubao-fix-continue/summary.md
```

注意：`docs/first-class.md` 在工作区已有改动，但本次未修改它。

## 关键实现

### 平台排序

`favorite_platform_sort_key()` 让 `douyin` 排序组为 `-1`、`tiktok` 排序组为 `1`，其它平台为 `0`。

### 前端续跑

localStorage key：

```text
starmind.batchTitleFilter.{platform}
```

启用平台：

```text
douyin, xiaohongshu, bilibili
```

支持 stage：

```text
scanned, classified, prepared, completed
```

### 豆包闭环

- 输入框：覆盖 textarea、contenteditable、role textbox、ProseMirror、composer/input/chat 容器。
- 发送按钮：覆盖 button、role button、send/submit class、svg/use。
- 发送确认失败统一返回：

```text
doubao_send_not_confirmed
```

## 自动化验证结果

在 `/Users/wyy/ ai/starmind/StarMind-main/StarMind` 下执行通过：

```bash
PYTHONPATH=. ../.venv311/bin/python -m py_compile app/api/routes.py app/connectors/doubao_extractor.py
```

```bash
PYTHONPATH=. ../.venv311/bin/python -m pytest tests/test_sync_favorites_page.py tests/test_doubao_extract_selected.py -v
```

结果：

```text
24 passed, 1 warning
```

```bash
PYTHONPATH=. ../.venv311/bin/python -m pytest tests/test_batch_title_filtering.py tests/test_favorite_scan_entrypoints.py tests/test_favorite_title_extractors.py -v
```

结果：

```text
33 passed, 1 warning
```

## 浏览器验证结果

通过 CDP 验证：

- 8000 端口已重启为新版本 `/StarMind` 代码。
- `/ui/sync` 渲染顺序：抖音第一、TikTok 最后，TikTok 图标 URL 存在。
- `/ui/source-setup/douyin` 页面加载新 `app.js`，包含 `starmind.batchTitleFilter` 续跑逻辑。
- 注入 `stage=scanned` 后刷新页面，扫描结果恢复，AI 分类按钮可用，提取按钮禁用。
- 注入 `stage=classified` 后刷新页面，分类分组恢复，勾选状态恢复，提取按钮可用。

未直接执行真实豆包生成入库，因为这依赖豆包登录态、外部页面稳定性和实际内容生成时长；本次通过单元测试覆盖发送确认、等待排除 prompt、失败 metadata 和批处理不中断。

## 风险与后续建议

- 豆包网页 DOM 可能继续变化，当前 selector 已扩展，但仍建议后续真实运行时保留失败 metadata 观察具体 `doubao_error`。
- 续跑状态保存在浏览器 localStorage，换浏览器或清理站点数据后不会恢复。
- 若分类结果中的对象引用和扁平数组对象不一致，已通过 URL fallback 匹配 checkbox index，降低恢复后勾选无法映射的风险。
