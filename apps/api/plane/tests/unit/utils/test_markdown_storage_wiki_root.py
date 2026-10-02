# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""wiki 侧的第二条落盘路径。

项目页面镜像在 `MARKDOWN_STORAGE_PATH`（`…/ObsidianVault/2-项目`）下，
wiki 集合页面镜像在 `WIKI_MARKDOWN_STORAGE_PATH`（`…/ObsidianVault/3-Wiki`）下。
两者是**并列的两棵树**，不是一棵树的两个子目录：目录改号过（`2-项目`/`3-Wiki`），
把根写死成同一个相对位置会让改一次要动两处语义。

`markdown_storage.get_wiki_markdown_root` 在没有该环境变量时回落到
`get_markdown_root().parent / "3-Wiki"` —— 这样测试里那个 autouse 的
`isolate_markdown_mirror` 夹具（把 `MARKDOWN_STORAGE_PATH` 指到 tmp_path）
自动把 wiki 根也隔离到 tmp_path 下，不会有测试写到真实 vault。"""

import pytest

from plane.utils.markdown_storage import (
    WIKI_MARKDOWN_STORAGE_PATH_ENV,
    get_wiki_markdown_root,
    wiki_page_markdown_path,
    write_wiki_page_markdown,
)


@pytest.mark.unit
class TestGetWikiMarkdownRoot:
    def test_explicit_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(WIKI_MARKDOWN_STORAGE_PATH_ENV, str(tmp_path / "3-Wiki"))
        assert get_wiki_markdown_root() == tmp_path / "3-Wiki"

    def test_falls_back_next_to_the_projects_root(self, isolate_markdown_mirror):
        """`isolate_markdown_mirror` 把项目根设成 `<tmp>/markdown-mirror`，
        所以回落值必须是 `<tmp>/3-Wiki`（父目录的兄弟，不是它的子目录）。"""
        assert get_wiki_markdown_root() == isolate_markdown_mirror.parent / "3-Wiki"

    def test_the_two_roots_are_independent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARKDOWN_STORAGE_PATH", str(tmp_path / "2-项目"))
        monkeypatch.setenv(WIKI_MARKDOWN_STORAGE_PATH_ENV, str(tmp_path / "别处"))
        assert get_wiki_markdown_root() == tmp_path / "别处"


@pytest.mark.unit
class TestWikiPageMarkdownPath:
    def test_top_level_page_sits_under_the_collection_folder(self, isolate_markdown_mirror):
        path = wiki_page_markdown_path("Claude Code", "col-1", [], "安装与更新", "page-1")
        assert path == isolate_markdown_mirror.parent / "3-Wiki" / "Claude Code" / "安装与更新.md"

    def test_collection_name_is_sanitized(self, isolate_markdown_mirror):
        path = wiki_page_markdown_path("a/b:c", "col-1", [], "页", "page-1")
        assert path.parent.name == "a-b-c"

    def test_missing_name_falls_back_to_the_id(self, isolate_markdown_mirror):
        path = wiki_page_markdown_path("C", "col-1", [], "", "page-1")
        assert path.name == "page-1.md"

    def test_ancestors_become_folders(self, isolate_markdown_mirror):
        path = wiki_page_markdown_path("C", "col-1", [("父页", "p-0")], "子页", "p-1")
        assert path.parent.name == "父页"
        assert path.name == "子页.md"

    def test_name_collision_with_a_different_page_does_not_overwrite(self, isolate_markdown_mirror, tmp_path):
        first = wiki_page_markdown_path("C", "col-1", [], "同名", "aaaaaaaa-1")
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("---\nid: bbbbbbbb-2\n---\n\n别人的正文\n", encoding="utf-8")
        second = wiki_page_markdown_path("C", "col-1", [], "同名", "cccccccc-3")
        assert second.name == "同名-cccccccc.md"


@pytest.mark.unit
class TestWriteWikiPageMarkdown:
    def test_writes_frontmatter_and_body(self, isolate_markdown_mirror):
        write_wiki_page_markdown(
            collection_name="C",
            collection_id="col-1",
            ancestors=[],
            page_id="p-1",
            name="页",
            markdown="正文。",
        )
        written = isolate_markdown_mirror.parent / "3-Wiki" / "C" / "页.md"
        lines = written.read_text(encoding="utf-8").split("\n")
        assert lines[0] == "---"
        assert lines[1] == "id: p-1"
        assert lines[2] == 'title: "页"'
        assert lines[3].startswith("updated_at: ")
        assert lines[4] == "---"
        assert lines[5] == ""
        assert lines[6:] == ["正文。"]

    def test_second_write_preserves_a_clipped_notes_frontmatter(self, isolate_markdown_mirror):
        target = isolate_markdown_mirror.parent / "3-Wiki" / "C" / "剪藏.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\ntags:\n  - 罗盘\nsource: https://x\n---\n\n老正文\n", encoding="utf-8")

        write_wiki_page_markdown(
            collection_name="C",
            collection_id="col-1",
            ancestors=[],
            page_id="p-1",
            name="剪藏",
            markdown="新正文",
        )
        text = target.read_text(encoding="utf-8")
        assert "  - 罗盘" in text
        assert "source: https://x" in text
        assert text.count("---") == 2
        assert text.endswith("新正文")
