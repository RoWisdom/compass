# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Markdown → HTML 转换器（`plane.utils.markdown_to_html`）。

它是 `html_to_markdown` 的**逆**，所以这里既测「Markdown 变成什么 HTML」，
也测一条**往返**：转换器产出的 HTML 再经 `html_to_markdown` 应当回到原文。
往返那几条是这份测试的主体 —— 单独的正向断言只能证明「产出了某种 HTML」，
证明不了「对面认得」。"""

import pytest

from plane.utils.html_to_markdown import html_to_markdown
from plane.utils.markdown_to_html import markdown_to_html


@pytest.mark.unit
class TestBlocks:
    def test_heading_levels(self):
        assert markdown_to_html("## 二级") == "<h2>二级</h2>"

    def test_paragraph_wraps_in_p(self):
        assert markdown_to_html("一句话。") == "<p>一句话。</p>"

    def test_horizontal_rule(self):
        assert markdown_to_html("---") == "<hr>"

    def test_fenced_code_block_keeps_language_and_escapes(self):
        html = markdown_to_html("```python\na = 1 < 2\n```")
        assert html == '<pre><code class="language-python">a = 1 &lt; 2</code></pre>'

    def test_fenced_code_block_without_language(self):
        assert markdown_to_html("```\nplain\n```") == "<pre><code>plain</code></pre>"

    def test_table(self):
        html = markdown_to_html("| a | b |\n| --- | --- |\n| 1 | 2 |")
        assert html == (
            "<table><tbody>"
            "<tr><th>a</th><th>b</th></tr>"
            "<tr><td>1</td><td>2</td></tr>"
            "</tbody></table>"
        )

    def test_blockquote(self):
        assert markdown_to_html("> 引用") == "<blockquote><p>引用</p></blockquote>"

    def test_hr_between_paragraphs(self):
        """`---` 在正文里是 hr。实测目标 vault 真有（`Multica/快速安装.md` 3 条），
        所以不能一律当成分隔线跳过。"""
        assert markdown_to_html("正文\n\n---\n\n更多") == "<p>正文</p>\n<hr>\n<p>更多</p>"


@pytest.mark.unit
class TestLists:
    def test_unordered_list(self):
        assert markdown_to_html("- a\n- b") == "<ul>\n<li>a</li>\n<li>b</li>\n</ul>"

    def test_ordered_list(self):
        assert markdown_to_html("1. a\n2. b") == "<ol>\n<li>a</li>\n<li>b</li>\n</ol>"

    def test_nested_list(self):
        html = markdown_to_html("- a\n  - a1\n- b")
        assert html == "<ul>\n<li>a\n<ul>\n<li>a1</li>\n</ul></li>\n<li>b</li>\n</ul>"

    def test_task_list_shape_matches_the_inverse_converter(self):
        """`html_to_markdown` 认的是 `data-type="taskItem"` + `input[type=checkbox]`
        + 直接子 `div`。形状必须逐字对上，否则往返丢勾选状态。"""
        html = markdown_to_html("- [x] 做完\n- [ ] 没做")
        assert 'data-type="taskItem"' in html
        assert '<input type="checkbox" checked="checked">' in html
        assert '<input type="checkbox">' in html

    def test_loose_list_is_one_list_not_two(self):
        """Obsidian 常见「条目间空行」的松列表。空行不能把一张表切成两张。"""
        html = markdown_to_html("- a\n\n- b")
        assert html.count("<ul>") == 1


@pytest.mark.unit
class TestInline:
    def test_bold_italic_strike(self):
        assert markdown_to_html("**b** *i* ~~s~~") == "<p><strong>b</strong> <em>i</em> <s>s</s></p>"

    def test_inline_code(self):
        assert markdown_to_html("`a < b`") == "<p><code>a &lt; b</code></p>"

    def test_link(self):
        assert markdown_to_html("[t](https://a.b)") == '<p><a href="https://a.b">t</a></p>'

    def test_image(self):
        assert markdown_to_html("![alt](https://a.b/i.png)") == '<p><img src="https://a.b/i.png" alt="alt"></p>'

    def test_snake_case_is_not_italic(self):
        """技术笔记里 `snake_case` 满地都是，`_` 强调必须要求词边界。"""
        assert markdown_to_html("use snake_case here") == "<p>use snake_case here</p>"

    def test_standalone_underscore_emphasis_still_works(self):
        assert markdown_to_html("_斜_") == "<p><em>斜</em></p>"

    def test_raw_html_is_escaped_not_passed_through(self):
        """设计 §3.5 的保底：解析不了就转义成文本，样式丢、内容不丢。"""
        assert markdown_to_html("<div>裸标签</div>") == "<p>&lt;div&gt;裸标签&lt;/div&gt;</p>"

    def test_wikilink_stays_visible_as_text(self):
        assert markdown_to_html("[[某笔记]]") == "<p>[[某笔记]]</p>"


@pytest.mark.unit
class TestRoundTrip:
    """产出必须能被对面（`html_to_markdown`）读回来。"""

    @pytest.mark.parametrize(
        "markdown",
        [
            "# 标题",
            "普通段落。",
            "- a\n- b",
            "1. a\n2. b",
            "> 引用",
            "| a | b |\n| --- | --- |\n| 1 | 2 |",
            # `*` 强调必须写成 `_`：对面的 `html_to_markdown` 把 `<em>` 一律输出成 `_x_`
            # （`html_to_markdown.py:169`），所以往返的**不动点**是 `_` 而不是 `*`。
            # 这是对面既有的规范，不是本转换器的取舍 —— 别为了「对称」去改它。
            "_粗_ 与 _斜_ 与 `代码`",
            "[链接](https://a.b)",
            "```python\nprint(1)\n```",
            "- [x] 做完",
        ],
    )
    def test_html_to_markdown_returns_the_original(self, markdown):
        assert html_to_markdown(markdown_to_html(markdown)) == markdown

    def test_multiline_paragraph_round_trips_exactly(self):
        # 软换行是**往返精确**的（注释 :242-243 的断言）：段落内换行原样回到原文。
        # 硬换行不是（见 TestHardBreak）—— <br> 回来是普通换行，这是对面的既有限制。
        source = "第一行\n第二行\n第三行"
        assert html_to_markdown(markdown_to_html(source)) == source


@pytest.mark.unit
class TestHardBreak:
    """行尾双空格 / 未转义反斜杠是硬换行 —— 这两条分支原先都是坏的
    （双空格不可达，反斜杠会被漏进正文），本组用例把它们钉住。"""

    def test_two_trailing_spaces_become_a_br(self):
        assert markdown_to_html("a  \nb") == "<p>a<br>\nb</p>"

    def test_two_trailing_spaces_differ_from_a_soft_break(self):
        assert markdown_to_html("a  \nb") != markdown_to_html("a\nb")

    def test_trailing_backslash_becomes_a_br_and_is_consumed(self):
        # 那个反斜杠是标记，不是正文 —— 漏出来就是凭空多一个字符。
        assert markdown_to_html("a\\\nb") == "<p>a<br>\nb</p>"

    def test_one_trailing_space_is_a_soft_break(self):
        assert markdown_to_html("a \nb") == "<p>a\nb</p>"

    def test_escaped_backslash_is_not_a_hard_break(self):
        # 偶数个反斜杠 = 转义出来的字面反斜杠，不能吃掉、也不该产生 <br>。
        # 只钉「不产生 <br>」，不钉它转义后的形态（那是既有的另一回事）。
        assert "<br>" not in markdown_to_html("a\\\\\nb")

    def test_leading_indentation_is_dropped(self):
        assert markdown_to_html("  hello") == "<p>hello</p>"
