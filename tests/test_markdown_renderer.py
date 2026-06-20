from app.services.markdown_renderer import render_markdown


def test_render_markdown_outputs_structured_safe_html():
    html = render_markdown(
        "# 标题\n\n"
        "这是 **重点** 和 `code`。\n\n"
        "- 第一条\n"
        "- 第二条\n\n"
        "[链接](https://example.com)"
    )

    assert "<h1>标题</h1>" in html
    assert "<strong>重点</strong>" in html
    assert "<code>code</code>" in html
    assert "<ul><li>第一条</li><li>第二条</li></ul>" in html
    assert 'href="https://example.com"' in html
