# 同步收藏夹平台卡片顺序、抖音图标与说明文案调整任务计划

- [✓] Task 1: 调整同步页平台卡片数据
    - 1.1: 在 `app/api/routes.py` 定位 `PLATFORM_PRESETS` 中 `douyin` 配置
    - 1.2: 将抖音 `logo_url` 调整为白底可见的抖音图标地址
    - 1.3: 在 `/ui/sync` 路由构造 `cards` 后增加排序逻辑，使 `platform == "douyin"` 的卡片排到最后
    - 1.4: 保持其它平台原有相对顺序、状态、管理链接和说明数据不变

- [✓] Task 2: 调整同步页抖音卡片说明文案渲染
    - 2.1: 在 `app/templates/sync_favorites.html` 定位平台卡片内的 `<small>{{ platform.auth_hint }}</small>`
    - 2.2: 增加仅对 `platform.platform != "douyin"` 渲染说明文案的条件
    - 2.3: 保持卡片 DOM 层级、按钮、状态标签、标题和图标结构不变

- [✓] Task 3: 补充或更新页面测试
    - 3.1: 更新 `tests/test_sync_favorites_page.py`，断言 `/ui/sync` 中抖音卡片排在其它平台之后
    - 3.2: 断言抖音卡片渲染 `<img>` 图标
    - 3.3: 断言抖音卡片不渲染 `auth_hint` 说明文案
    - 3.4: 断言至少一个非抖音平台仍渲染说明文案

- [✓] Task 4: 运行验证并记录结果
    - 4.1: 运行 `PYTHONPATH=. .venv311/bin/python -m py_compile app/api/routes.py`
    - 4.2: 运行 `PYTHONPATH=. .venv311/bin/pytest tests/test_sync_favorites_page.py -v`
    - 4.3: 如本地 8000 服务需要刷新，重启或确认当前服务加载最新代码
    - 4.4: 访问 `/ui/sync`，确认抖音卡片在最后、图标可见、说明文案隐藏

- [✓] Task 5: 记录修改总结
    - 5.1: 更新 `.comate/specs/sync-platform-card-adjustment/tasks.md` 中已完成任务复选框
    - 5.2: 生成 `.comate/specs/sync-platform-card-adjustment/summary.md`，记录修改文件、验证结果和页面表现
