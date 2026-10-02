# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""frontmatter 读写（`plane.utils.markdown_storage`）。

**这不是新功能，是修一个既有缺陷**：`write_page_markdown` 原来把 frontmatter
无条件拼在文件头（`content = f"{_build_frontmatter(...)}{markdown}"`），从不读文件里
已有的那一块。实测目标 vault 里 27 篇有 20 篇**本来就带 frontmatter**
（Obsidian Web Clipper 剪藏的 `tags` / `source` / `created`），照原逻辑写一次就会
变成两块 `---...---`，用户原有的键连同其后的内容被降级成正文。

所以这里锁死三条：① 无 frontmatter 时行为与旧版**逐字一致**；② 有 frontmatter 时
用户的键一个不少，只有 Plane 自己那三个被覆盖；③ 畸形时不丢内容。"""

import pytest

from plane.utils.markdown_storage import (
    MANAGED_FRONTMATTER_KEYS,
    merge_frontmatter,
    split_frontmatter,
)

CLIPPED = (
    "---\n"
    "tags:\n"
    "  - 罗盘\n"
    "  - 工具\n"
    "created: 2026-05-31\n"
    "source: https://example.com/a\n"
    "title: 剪藏标题\n"
    "---\n"
    "\n"
    "正文第一段。\n"
)


@pytest.mark.unit
class TestSplitFrontmatter:
    def test_splits_keys_from_body(self):
        keys, body = split_frontmatter(CLIPPED)
        assert keys == [
            "tags:",
            "  - 罗盘",
            "  - 工具",
            "created: 2026-05-31",
            "source: https://example.com/a",
            "title: 剪藏标题",
        ]
        assert body == "正文第一段。\n"

    def test_no_frontmatter_returns_the_whole_text_as_body(self):
        keys, body = split_frontmatter("就是一段话。\n")
        assert keys == []
        assert body == "就是一段话。\n"

    def test_unterminated_block_is_treated_as_body(self):
        """首行是 `---` 但再没有 `---` ⇒ 宁可当成正文，也不要吞掉整篇。"""
        text = "---\ntags: [a]\n后面还有内容\n"
        keys, body = split_frontmatter(text)
        assert keys == []
        assert body == text

    def test_none_is_empty(self):
        assert split_frontmatter(None) == ([], "")


@pytest.mark.unit
class TestMergeFrontmatter:
    def test_without_existing_matches_the_old_shape(self):
        """无 frontmatter 时必须与旧行为逐字一致（旧格式：只有 id/title/updated_at）。"""
        merged = merge_frontmatter(None, title="我的页", entry_id="abc-123", body="正文。")
        lines = merged.split("\n")
        assert lines[0] == "---"
        assert lines[1] == "id: abc-123"
        assert lines[2] == 'title: "我的页"'
        assert lines[3].startswith("updated_at: ")
        assert lines[4] == "---"
        assert lines[5] == ""
        assert lines[6:] == ["正文。"]

    def test_without_title_omits_the_title_line(self):
        merged = merge_frontmatter(None, title=None, entry_id="abc", body="x")
        assert "title:" not in merged

    def test_keeps_every_user_key_and_overrides_only_managed_ones(self):
        merged = merge_frontmatter(CLIPPED, title="Plane 里的新名字", entry_id="page-1", body="新正文。")
        assert "tags:" in merged
        assert "  - 罗盘" in merged
        assert "created: 2026-05-31" in merged
        assert "source: https://example.com/a" in merged
        assert "id: page-1" in merged
        assert 'title: "Plane 里的新名字"' in merged
        assert "剪藏标题" not in merged, "被管的 title 必须被覆盖，不是并列两份"
        assert merged.count("---") == 2, "只能有一块 frontmatter"
        assert merged.endswith("新正文。")

    def test_body_is_replaced_not_appended(self):
        merged = merge_frontmatter(CLIPPED, title="t", entry_id="e", body="只留这句。")
        assert "正文第一段。" not in merged

    def test_managed_keys_are_exactly_the_three(self):
        assert MANAGED_FRONTMATTER_KEYS == ("id", "title", "updated_at")

    def test_indented_lines_are_never_read_as_keys(self):
        """`  - 罗盘` 这种缩进行必须原样保留，不能被当成键删掉。"""
        merged = merge_frontmatter(CLIPPED, title="t", entry_id="e", body="b")
        assert "  - 工具" in merged

    def test_managed_key_with_a_block_scalar_takes_its_continuation_with_it(self):
        existing = "---\ntitle: |\n  第一行\n  第二行\ntags: [a]\n---\n\n旧正文\n"
        merged = merge_frontmatter(existing, title="新", entry_id="e", body="新正文")
        assert "第一行" not in merged
        assert "tags: [a]" in merged

    def test_malformed_existing_still_produces_one_valid_block(self, caplog):
        """畸形（首行 `---`、无闭合）⇒ 退化成「另起一块 + 写新正文」，并记一条 warning。

        **畸形块本身那几行会被丢掉** —— 它解析不出来，留着只会让文件里出现两个块。
        丢的是**已经坏掉的文件**里那几行 YAML，不是正文；正文（Plane 的那一份）照写。
        这就是设计 §3.4 定的失败方向：宁可丢坏块，也不能让正文写不进去。
        """
        with caplog.at_level("WARNING"):
            merged = merge_frontmatter("---\ntags: [a]\n没有闭合", title="t", entry_id="e", body="正文")
        assert merged.count("---") == 2
        assert "id: e" in merged
        assert merged.endswith("正文")
        assert any("frontmatter" in record.message for record in caplog.records)
