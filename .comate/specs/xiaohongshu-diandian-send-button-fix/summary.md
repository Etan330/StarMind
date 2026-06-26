# 小红书点点发送按钮误点加号修复总结

## 根因

通过浏览器 CDP 检查真实 `https://www.xiaohongshu.com/ai_chat` DOM 后确认：

- 左下角加号是 `button.ai-input-action-btn`，内部图标为 `#addM`，坐标约为 `left=403 top=327 right=435 bottom=359`。
- 右下角发送箭头不是 `button`，而是 `div.submit-button-wrapper` / `.bottom-box-right`，内部图标为 `#arrow_top`，坐标约为 `left=1161 top=327 right=1193 bottom=359`。
- 旧逻辑只查找 `button, [role="button"]`，因此根本看不到真实发送箭头，只能选到左下角加号。

## 修复内容

修改文件：

- `app/connectors/xiaohongshu_diandian_extractor.py`
- `tests/test_xiaohongshu_diandian_extractor.py`
- `.comate/specs/xiaohongshu-diandian-send-button-fix/tasks.md`

具体修复：

- `_send_prompt()` 的发送候选从 `button, [role="button"]` 扩展为：
  - `.submit-button-wrapper`
  - `.submit-button`
  - `.bottom-box-right`
  - `.bottom-box-right-submit-button`
  - `svg.submit-button`
- 读取候选内部 `use[href]` / `use[xlink:href]`。
- 对 `#arrow_top`、`.submit-button-wrapper`、右下角提交区域加高分。
- 对 `#addM`、`.ai-input-action-btn`、`.bottom-box-left`、左下角区域大幅扣分。
- 如果没有找到右下角发送箭头，不再点击加号兜底，而是返回 `send_button_not_found`。
- 继续保留发送后确认逻辑：输入框清空 / 消息数量增加 / 页面出现用户消息后才认为发送成功。

## 自动化测试

先新增失败测试，旧逻辑失败点：

```text
assert ".submit-button-wrapper" in send_script
```

修复后执行：

```bash
PYTHONPATH=. .venv311/bin/pytest tests/test_xiaohongshu_diandian_extractor.py tests/test_xiaohongshu_diandian_extract_selected.py tests/test_doubao_extract_selected.py -v
```

结果：

```text
29 passed, 1 warning in 46.96s
```

## 浏览器验证

在真实点点页面运行选择逻辑但不发送，返回：

```json
{
  "selected": {
    "score": 1930,
    "hint": "bottom-box-right #arrow_top reds-icon submit-button btn-wrapper",
    "cls": "bottom-box-right",
    "use": "#arrow_top",
    "rect": {"left": 1161, "top": 327, "right": 1193, "bottom": 359, "width": 32, "height": 32}
  }
}
```

验证结论：

- 新逻辑选择的是右下角 `#arrow_top` 发送箭头。
- 没有选择左下角 `#addM` 加号。
- 点击中心约为 `x=1177, y=343`。

## 服务状态

已重启本地服务：

```text
http://127.0.0.1:8000/ui/source-setup/xiaohongshu -> 200
```

最新代码已被本地 uvicorn 加载。
