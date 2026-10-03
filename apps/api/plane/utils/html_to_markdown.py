# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Converts Plane's TipTap/ProseMirror description HTML into Markdown, mirroring the
# frontend's convertHTMLToMarkdown (packages/utils/src/editor/markdown-parser/root.ts)
# closely enough for a local .md mirror: standard block/inline HTML, GFM task lists,
# tables and code fences, plus Plane's custom <mention-component> / <image-component>.

# Python imports
from typing import Callable, Optional

# Third Party imports
from bs4 import BeautifulSoup, NavigableString, Tag


class _Context:
    """Optional resolvers supplied by the caller (e.g. a Django management command)."""

    def __init__(
        self,
        resolve_user: Optional[Callable[[str], Optional[str]]] = None,
        resolve_asset_url: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.resolve_user = resolve_user
        self.resolve_asset_url = resolve_asset_url


def html_to_markdown(
    html: Optional[str],
    *,
    resolve_user: Optional[Callable[[str], Optional[str]]] = None,
    resolve_asset_url: Optional[Callable[[str], Optional[str]]] = None,
) -> str:
    """Convert an issue description HTML string into Markdown."""
    ctx = _Context(resolve_user=resolve_user, resolve_asset_url=resolve_asset_url)
    soup = BeautifulSoup(html or "", "html.parser")
    root = soup.body if soup.body is not None else soup
    blocks = _render_children_as_blocks(root, ctx)
    return "\n\n".join(blocks).strip()


def _render_children_as_blocks(node, ctx: _Context):
    blocks = []
    for child in node.children:
        if isinstance(child, Tag):
            rendered = _render_block(child, ctx)
            if rendered:
                blocks.append(rendered)
        elif isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                blocks.append(text)
    return blocks


def _render_block(tag: Tag, ctx: _Context) -> str:
    name = tag.name
    if name == "p":
        return _inline_children(tag, ctx).strip()
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return "#" * int(name[1]) + " " + _inline_children(tag, ctx).strip()
    if name in ("ul", "ol"):
        return _render_list(tag, ctx, 0)
    if name == "blockquote":
        inner = "\n\n".join(_render_children_as_blocks(tag, ctx))
        return "\n".join(("> " + line) if line else ">" for line in inner.split("\n"))
    if name == "pre":
        return _render_pre(tag)
    if name == "hr":
        return "---"
    if name == "table":
        return _render_table(tag, ctx)
    # Wrapper / unknown elements (div, custom components): unwrap their children.
    return "\n\n".join(_render_children_as_blocks(tag, ctx))


def _render_list(tag: Tag, ctx: _Context, indent: int) -> str:
    ordered = tag.name == "ol"
    lines = []
    index = 1
    for child in tag.children:
        if not isinstance(child, Tag) or child.name != "li":
            continue
        marker = f"{index}." if ordered else "-"
        index += 1
        lines.append(_render_li(child, ctx, marker, indent))
    return "\n".join(lines)


def _render_li(li: Tag, ctx: _Context, marker: str, indent: int) -> str:
    prefix = " " * indent
    if li.get("data-type") == "taskItem":
        checkbox = li.find("input", {"type": "checkbox"})
        checked = bool(checkbox and checkbox.has_attr("checked"))
        content_div = li.find("div", recursive=False)
        text = _inline_children(content_div, ctx).strip() if content_div else ""
        return f"{prefix}{marker} [{'x' if checked else ' '}] {text}"

    inline_parts = []
    nested = []
    for child in li.children:
        if isinstance(child, Tag):
            if child.name in ("ul", "ol"):
                nested.append(child)
            else:
                inline_parts.append(_inline_node(child, ctx))
        elif isinstance(child, NavigableString):
            inline_parts.append(str(child))

    line = f"{prefix}{marker} {''.join(inline_parts).strip()}"
    for nested_list in nested:
        line += "\n" + _render_list(nested_list, ctx, indent + 2)
    return line


def _render_pre(tag: Tag) -> str:
    code = tag.find("code")
    lang = ""
    if code:
        for cls in code.get("class") or []:
            if isinstance(cls, str) and cls.startswith("language-"):
                lang = cls[len("language-") :]
                break
    text = (code.get_text() if code else tag.get_text()).rstrip("\n")
    return f"```{lang}\n{text}\n```"


def _render_table(tag: Tag, ctx: _Context) -> str:
    rows = tag.find_all("tr")
    if not rows:
        return ""

    def cells(row):
        return [_inline_children(cell, ctx).strip().replace("\n", " ") for cell in row.find_all(["th", "td"])]

    header = cells(rows[0])
    col_count = max([len(header), *[len(cells(r)) for r in rows]])
    header += [""] * (col_count - len(header))

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * col_count) + " |",
    ]
    for row in rows[1:]:
        body = cells(row)
        body += [""] * (col_count - len(body))
        lines.append("| " + " | ".join(body) + " |")
    return "\n".join(lines)


def _inline_node(node, ctx: _Context) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if isinstance(node, Tag):
        return _inline_tag(node, ctx)
    return ""


def _inline_children(node, ctx: _Context) -> str:
    return "".join(_inline_node(child, ctx) for child in node.children)


def _inline_tag(tag: Tag, ctx: _Context) -> str:
    name = tag.name
    if name in ("strong", "b"):
        return "**" + _inline_children(tag, ctx) + "**"
    if name in ("em", "i"):
        return "_" + _inline_children(tag, ctx) + "_"
    if name in ("s", "del", "strike"):
        return "~~" + _inline_children(tag, ctx) + "~~"
    if name == "u":
        return _inline_children(tag, ctx)
    if name == "code":
        text = tag.get_text()
        tick = "``" if "`" in text else "`"
        return f"{tick}{text}{tick}"
    if name == "a":
        return f"[{_inline_children(tag, ctx)}]({tag.get('href', '')})"
    if name == "img":
        src, alt = tag.get("src"), tag.get("alt")
        # alt 缺省或为空**不是**丢弃这张图的理由：``markdown_to_html`` 对 `![](url)`
        # 产出的就是 ``alt=""``，若这里要求 alt 非空，两个方向就不再互逆 —— 用户笔记里的
        # 无 alt 图片会在**每次正文保存**时静默消失（DB 里的 HTML 还在，文件已经丢了）。
        # 样式可以丢，内容不可以。
        return f"![{alt or ''}]({src})" if src else ""
    if name == "br":
        return "\n"
    if name == "mention-component":
        return _mention(tag, ctx)
    if name == "image-component":
        return _image_component(tag, ctx)
    # span / label / p / div / unknown inline-ish tags: unwrap.
    return _inline_children(tag, ctx)


def _mention(tag: Tag, ctx: _Context) -> str:
    entity_name = tag.get("entity_name")
    entity_id = tag.get("entity_identifier")
    if entity_name == "user_mention" and entity_id and ctx.resolve_user:
        display_name = ctx.resolve_user(entity_id)
        if display_name:
            return f"@{display_name}"
    return f"@{entity_id}" if entity_id else "@"


def _image_component(tag: Tag, ctx: _Context) -> str:
    src = tag.get("src")
    if not src:
        return ""
    if src.startswith(("http://", "https://")):
        return f"![]({src})"
    url = ctx.resolve_asset_url(src) if ctx.resolve_asset_url else None
    # 解析不出 URL（资产文件不在了、或调用方没给 resolver）时**保留原始引用**而不是整段丢掉：
    # 落到笔记里的会是一张失效的图，但「这里原来有张图」这件事留下来了，用户能自己修。
    # 宁可丢样式，不可丢内容。
    return f"![]({url or src})"
