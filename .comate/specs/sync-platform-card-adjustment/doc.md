# 同步收藏夹平台卡片顺序、抖音图标与说明文案调整设计

## 需求场景

用户在 `/ui/sync` 页面选中了「收藏来源列表」中的平台卡片区域，并提出三条元素级修改要求：

1. 对卡片左侧 `<img>` 元素：添加抖音的图标。
2. 对整条 `<article class="source-line">` 元素：将抖音卡片移到最后，下方平台卡片上移。
3. 对卡片内 `<small>` 元素：不要下面这一段话。

根据 DOM 路径：

```text
body > div.app-shell > main.content-shell > section.page-surface > section.glass-card.v1-section > div.compact-list > article.source-line
```

已定位目标源码为：

```text
/Users/wyy/ ai/starmind/StarMind-main/app/templates/sync_favorites.html
```

该页面由后端 `/ui/sync` 路由提供 `favorite_platforms` 数据，相关数据源在：

```text
/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py
```

## 技术方案

采用最小变更方案，不新增 DOM 层级，不调整模板整体结构，只修改目标平台卡片对应的数据与渲染条件。

### 1. 抖音图标

当前平台图标来自 `platform.logo_url`：

```jinja2
{% if platform.logo_url %}
  <img src="{{ platform.logo_url }}" alt="{{ platform.name }}" width="24" height="24" style="border-radius:4px;">
{% endif %}
```

抖音数据来自 `app/api/routes.py` 中 `PLATFORM_PRESETS` 的 `douyin.logo_url`。当前值为 TikTok simpleicons 白色图标，不适合当前白底卡片展示。

修改方式：仅将 `douyin` 的 `logo_url` 改为更明确可见的抖音图标地址，例如：

```python
"logo_url": "https://cdn.simpleicons.org/douyin/000000",
```

若 simpleicons 对 `douyin` slug 不可用，则采用当前项目可接受的稳定图标源或保留可见 TikTok 黑色图标。优先确保页面显示一个可见的抖音/短视频平台图标。

### 2. 抖音卡片移到最后

当前 `/ui/sync` 页面 cards 顺序由后端构造 `cards` 后传给模板：

```python
favorite_platforms=cards
```

修改方式：在 `/ui/sync` 路由中仅对 `cards` 做排序，让 `platform == "douyin"` 的卡片排到最后，其它平台保持原有相对顺序。

建议实现：

```python
cards.sort(key=lambda item: item["platform"] == "douyin")
```

这样能满足「移到最后，下方的上移」，且不会改变模板 DOM 层级。

### 3. 删除抖音卡片下方说明文字

目标 `<small>` 源码：

```jinja2
<small style="display:block;color:#888;">{{ platform.auth_hint }}</small>
```

修改方式：仅对抖音卡片隐藏该说明行，其它平台保持不变：

```jinja2
{% if platform.platform != "douyin" %}
  <small style="display:block;color:#888;">{{ platform.auth_hint }}</small>
{% endif %}
```

这符合元素级记录指向的当前卡片，不扩散到所有平台。

## 受影响文件

### `/Users/wyy/ ai/starmind/StarMind-main/app/api/routes.py`

修改类型：小范围数据/排序调整。

受影响位置：

- `PLATFORM_PRESETS` 中 `douyin` 的 `logo_url` 字段。
- `/ui/sync` 对 `cards` 的构造与传参逻辑。

预期修改：

- 抖音图标变为可见图标。
- 抖音平台卡片在同步收藏夹列表中排到最后。

### `/Users/wyy/ ai/starmind/StarMind-main/app/templates/sync_favorites.html`

修改类型：Jinja 条件渲染调整。

受影响位置：

- 平台卡片内 `<small>{{ platform.auth_hint }}</small>`。

预期修改：

- 抖音卡片不显示 `auth_hint` 说明文字。
- 其它平台仍显示原说明文字。

## 边界条件

- 不新增元素。
- 不删除平台卡片。
- 不调整 `article.source-line` 内部 DOM 层级。
- 不影响 `/ui/source-setup/{platform}` 管理链接。
- 不改变其它页面的 `source-line` 样式。
- 不修改无关平台的图标、状态、按钮或说明。

## 数据流

```text
PLATFORM_PRESETS
  -> /ui/sync route 构造 cards
  -> cards 排序：douyin 最后
  -> sync_favorites.html 渲染 favorite_platforms
  -> 平台卡片展示图标、标题、状态、管理按钮
  -> douyin 卡片跳过 auth_hint small 文案
```

## 验证方式

1. 运行相关页面测试：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_sync_favorites_page.py -v
```

2. 必要时运行 Python 编译检查：

```bash
PYTHONPATH=. .venv311/bin/python -m py_compile app/api/routes.py
```

3. 若本地服务可用，用浏览器或 HTTP 检查 `/ui/sync`：

- 抖音卡片显示图标。
- 抖音卡片位于列表最后。
- 抖音卡片不显示 `auth_hint` 说明文字。
- 其它平台说明文字仍存在。

## 预期结果

- `/ui/sync` 中抖音卡片从首位移动到最后。
- 原本下方的平台卡片自然上移。
- 抖音卡片左侧显示清晰图标。
- 抖音卡片下方说明文字被隐藏。
- 其它平台卡片保持原有展示逻辑。