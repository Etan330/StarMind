from __future__ import annotations

import html
import re


_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def render_markdown(markdown: str | None) -> str:
    """Render the small Markdown subset StarMind writes into safe HTML."""
    if not markdown:
        return ""

    lines = str(markdown).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html_blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = html.escape(stripped[3:].strip())
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            class_attr = f' class="language-{language}"' if language else ""
            html_blocks.append(f"<pre><code{class_attr}>{html.escape(chr(10).join(code_lines))}</code></pre>")
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            html_blocks.append(f"<h{level}>{_render_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if stripped in {"---", "***", "___"}:
            html_blocks.append("<hr>")
            index += 1
            continue

        if _is_unordered(stripped):
            items: list[str] = []
            while index < len(lines) and _is_unordered(lines[index].strip()):
                items.append(f"<li>{_render_inline(lines[index].strip()[2:].strip())}</li>")
                index += 1
            html_blocks.append("<ul>" + "".join(items) + "</ul>")
            continue

        if _is_ordered(stripped):
            items = []
            while index < len(lines) and _is_ordered(lines[index].strip()):
                text = re.sub(r"^\d+\.\s+", "", lines[index].strip())
                items.append(f"<li>{_render_inline(text)}</li>")
                index += 1
            html_blocks.append("<ol>" + "".join(items) + "</ol>")
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip(">").strip())
                index += 1
            html_blocks.append(f"<blockquote>{_render_inline(' '.join(quote_lines))}</blockquote>")
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if (
                not next_line
                or next_line.startswith("```")
                or next_line.startswith("#")
                or next_line in {"---", "***", "___"}
                or _is_unordered(next_line)
                or _is_ordered(next_line)
                or next_line.startswith(">")
            ):
                break
            paragraph_lines.append(next_line)
            index += 1
        html_blocks.append(f"<p>{_render_inline(' '.join(paragraph_lines))}</p>")

    return "\n".join(html_blocks)


def _is_unordered(line: str) -> bool:
    return line.startswith("- ") or line.startswith("* ")


def _is_ordered(line: str) -> bool:
    return bool(re.match(r"^\d+\.\s+", line))


def _render_inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = _LINK_RE.sub(r'<a href="\2" target="_blank" rel="noreferrer">\1</a>', escaped)
    escaped = _CODE_RE.sub(r"<code>\1</code>", escaped)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)
    return escaped
