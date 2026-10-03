# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Converts Markdown into Plane's TipTap/ProseMirror description HTML. The two
# converters deliberately agree on the same subset — blocks, inline styling,
# lists, tables, links, code fences — so a .md mirror can be read back into an
# editor document. They are **not** exact inverses of each other, though: on the
# image axis both directions are deliberately tolerant so a picture is never
# dropped. This converter emits `alt=""` for `![](url)`, and html_to_markdown
# keeps an image whenever it has a src (empty alt, or no alt attribute at all),
# falling back to the raw src when an asset URL cannot be resolved — round-trip
# of an image is pinned by tests/unit/utils/test_markdown_to_html.py
# (TestImageRoundTrip). Mentions, underlines and Plane's custom image-component
# likewise survive as *content* (plain text / a plain <img>) rather than as the
# same markup. Anything this converter cannot parse degrades to escaped
# paragraph text — styling is lost, content never is.

# Python imports
import re
from typing import Optional

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*([\w+#-]*)\s*$")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_BULLET_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_TASK_RE = re.compile(r"^\[([ xX])\]\s+(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")

_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"))

# Ordered by precedence: code first (its body is not re-parsed), then images
# before links (they share the leading "["), then ** / __ before * / _.
# The `*` and `_` branches require a non-word character on the outside so that
# `snake_case` and `a*b` are left alone — a real hazard in technical notes.
_INLINE_RE = re.compile(
    r"(?P<code_tick>`+)(?P<code_body>.+?)(?P=code_tick)"
    r"|!\[(?P<img_alt>[^\]]*)\]\((?P<img_src>[^)\s]+)\)"
    r"|\[(?P<link_text>[^\]]*)\]\((?P<link_href>[^)\s]+)\)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|__(?P<bold2>.+?)__"
    r"|~~(?P<strike>.+?)~~"
    r"|(?<![*\w])\*(?P<italic>[^*]+?)\*(?!\*)"
    r"|(?<![_\w])_(?P<italic2>[^_]+?)_(?![_\w])"
)


def markdown_to_html(markdown: Optional[str]) -> str:
    """Convert a Markdown string into Plane's description HTML."""
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(_render_blocks(lines))


def _escape(text: str) -> str:
    for needle, replacement in _ESCAPES:
        text = text.replace(needle, replacement)
    return text


def _render_blocks(lines: list) -> list:
    blocks = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            index, block = _consume_fence(lines, index, fence)
            blocks.append(block)
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            index += 1
            continue

        # Before the list branches: `- - -` is a horizontal rule, not a bullet.
        if _HR_RE.match(line):
            blocks.append("<hr>")
            index += 1
            continue

        if _BLOCKQUOTE_RE.match(line):
            index, block = _consume_blockquote(lines, index)
            blocks.append(block)
            continue

        # A table needs BOTH a pipe on the line and a separator row under it that
        # itself contains a pipe — otherwise a paragraph followed by `---` would
        # be misread as a one-column table.
        if "|" in line and index + 1 < total and "|" in lines[index + 1] and _TABLE_SEP_RE.match(lines[index + 1]):
            index, block = _consume_table(lines, index)
            blocks.append(block)
            continue

        if _BULLET_RE.match(line) or _ORDERED_RE.match(line):
            index, block = _consume_list(lines, index)
            blocks.append(block)
            continue

        index, block = _consume_paragraph(lines, index)
        blocks.append(block)
    return blocks


def _consume_fence(lines: list, start: int, fence) -> tuple:
    marker = fence.group(1)
    language = fence.group(2) or ""
    body = []
    index = start + 1
    while index < len(lines) and not lines[index].strip().startswith(marker):
        body.append(lines[index])
        index += 1
    if index < len(lines):
        index += 1
    code = _escape("\n".join(body))
    class_attr = f' class="language-{_escape(language)}"' if language else ""
    return index, f"<pre><code{class_attr}>{code}</code></pre>"


def _consume_blockquote(lines: list, start: int) -> tuple:
    body = []
    index = start
    while index < len(lines):
        match = _BLOCKQUOTE_RE.match(lines[index])
        if match is None:
            break
        body.append(match.group(1))
        index += 1
    return index, "<blockquote>" + "\n".join(_render_blocks(body)) + "</blockquote>"


def _split_row(line: str) -> list:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _consume_table(lines: list, start: int) -> tuple:
    header = _split_row(lines[start])
    index = start + 2
    rows = ["<tr>" + "".join(f"<th>{_inline(cell)}</th>" for cell in header) + "</tr>"]
    while index < len(lines) and lines[index].strip() and "|" in lines[index]:
        cells = _split_row(lines[index])
        rows.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>")
        index += 1
    return index, "<table><tbody>" + "".join(rows) + "</tbody></table>"


def _consume_list(lines: list, start: int) -> tuple:
    items = []
    index = start
    total = len(lines)
    while index < total:
        line = lines[index]
        if not line.strip():
            # A blank line continues the list only when the next non-blank line is
            # still a list item. Obsidian's "loose lists" (a blank line between
            # items) are common and must not be split into two lists.
            lookahead = index + 1
            while lookahead < total and not lines[lookahead].strip():
                lookahead += 1
            if lookahead >= total or not (_BULLET_RE.match(lines[lookahead]) or _ORDERED_RE.match(lines[lookahead])):
                break
            index = lookahead
            continue

        bullet = _BULLET_RE.match(line)
        ordered = _ORDERED_RE.match(line)
        match = bullet or ordered
        if match is None:
            break

        indent = len(match.group(1).expandtabs(4))
        text = match.group(3).strip()
        checked = None
        if bullet:
            task = _TASK_RE.match(text)
            if task:
                checked = task.group(1).lower() == "x"
                text = task.group(2).strip()
        items.append((indent, bool(ordered), checked, text))
        index += 1

    html, _ = _build_list(items, 0, items[0][0])
    return index, html


def _build_list(items: list, index: int, indent: int) -> tuple:
    """Render one nesting level, recursing when an item is followed by a deeper one."""
    ordered = items[index][1]
    tag = "ol" if ordered else "ul"
    parts = [f"<{tag}>"]
    while index < len(items):
        item_indent, _item_ordered, checked, text = items[index]
        if item_indent != indent:
            break
        index += 1
        children = ""
        if index < len(items) and items[index][0] > indent:
            children_html, index = _build_list(items, index, items[index][0])
            children = "\n" + children_html

        if checked is None:
            parts.append(f"<li>{_inline(text)}{children}</li>")
        else:
            marker = ' checked="checked"' if checked else ""
            parts.append(
                f'<li data-type="taskItem"><label><input type="checkbox"{marker}>'
                f"<span></span></label><div><p>{_inline(text)}</p></div>{children}</li>"
            )
    parts.append(f"</{tag}>")
    return "\n".join(parts), index


def _starts_block(line: str) -> bool:
    return bool(
        _HEADING_RE.match(line)
        or _FENCE_RE.match(line)
        or _HR_RE.match(line)
        or _BULLET_RE.match(line)
        or _ORDERED_RE.match(line)
        or _BLOCKQUOTE_RE.match(line)
    )


def _consume_paragraph(lines: list, start: int) -> tuple:
    body = []
    index = start
    while index < len(lines) and lines[index].strip() and not _starts_block(lines[index]):
        body.append(lines[index].lstrip())
        index += 1

    # Lines are joined with a literal newline, not a space and not <br>: HTML
    # collapses it to a space when rendered (CommonMark's soft break), and
    # html_to_markdown keeps the newline verbatim, so the round trip is exact
    # for *soft* breaks.
    #
    # A trailing double-space or an unescaped trailing backslash is a *hard*
    # break and becomes <br>. Note this is not round-trip stable: the inverse
    # converter renders <br> back as a plain "\n" (html_to_markdown.py's _inline_tag),
    # i.e. a soft break. So a hard break survives as content but not as a break
    # character. That asymmetry belongs to the inverse converter and is out of
    # scope here — do not try to "fix" it from this side.
    parts = []
    for line in body:
        text = line.rstrip()
        # An odd number of trailing backslashes means the last one is the
        # hard-break marker (an even count is an escaped backslash — a literal —
        # so it must be left alone rather than eaten).
        if (len(text) - len(text.rstrip("\\"))) % 2 == 1:
            parts.append(_inline(text[:-1]) + "<br>")
        elif len(line) - len(text) >= 2:
            parts.append(_inline(text) + "<br>")
        else:
            parts.append(_inline(text))
    return index, "<p>" + "\n".join(parts) + "</p>"


def _inline(text: str) -> str:
    """Render one line's inline Markdown, escaping everything outside a match."""
    out = []
    position = 0
    for match in _INLINE_RE.finditer(text):
        out.append(_escape(text[position : match.start()]))
        out.append(_render_inline_match(match))
        position = match.end()
    out.append(_escape(text[position:]))
    return "".join(out)


def _render_inline_match(match) -> str:
    code_body = match.group("code_body")
    if code_body is not None:
        return f"<code>{_escape(code_body.strip())}</code>"

    img_src = match.group("img_src")
    if img_src is not None:
        return f'<img src="{_escape(img_src)}" alt="{_escape(match.group("img_alt"))}">'

    link_href = match.group("link_href")
    if link_href is not None:
        return f'<a href="{_escape(link_href)}">{_inline(match.group("link_text"))}</a>'

    for group, tag in (
        ("bold", "strong"),
        ("bold2", "strong"),
        ("strike", "s"),
        ("italic", "em"),
        ("italic2", "em"),
    ):
        value = match.group(group)
        if value is not None:
            return f"<{tag}>{_inline(value)}</{tag}>"

    return _escape(match.group(0))
