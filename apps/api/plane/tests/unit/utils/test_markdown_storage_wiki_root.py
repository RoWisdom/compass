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
    get_markdown_root,
    get_wiki_markdown_root,
    wiki_page_markdown_path,
    write_wiki_page_markdown,
)


@pytest.mark.unit
class TestGetWikiMarkdownRoot:
    def test_explicit_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(WIKI_MARKDOWN_STORAGE_PATH_ENV, str(tmp_path / "3-Wiki"))
        assert get_wiki_markdown_root() == tmp_path / "3-Wiki"

    def test_falls_back_next_to_the_projects_root(self, monkeypatch, tmp_path):
        """真正走回落分支：没有 `WIKI_MARKDOWN_STORAGE_PATH` 时，
        `get_wiki_markdown_root` 必须等于 `get_markdown_root().parent / "3-Wiki"`
        （父目录的兄弟，不是它的子目录）。

        autouse 的 `isolate_markdown_mirror` 夹具**总是**设了 wiki env（钉住两个根是
        故意的，曾挡住一次把镜像写进用户真实 vault 的事故，不要动它）。所以这里必须由
        **测试自己**删掉那个变量、再走回落——否则该函数永远走 env 分支，这条断言两侧
        都来自同一个 env 值，会退化成恒真的空转。
        """
        mirror_root = tmp_path / "markdown-mirror"
        monkeypatch.setenv("MARKDOWN_STORAGE_PATH", str(mirror_root))
        monkeypatch.delenv(WIKI_MARKDOWN_STORAGE_PATH_ENV, raising=False)

        # 守卫就是**下面这一句**：它要求 `get_wiki_markdown_root()` 等于
        # `get_markdown_root().parent / "3-Wiki"`，只有走了回落分支才成立。若将来有人
        # 又把 wiki env 塞回夹具/环境（或删掉这里的 delenv），本句会红 —— 原先它上方
        # 还有一句 `assert ... not in os.environ`，但那句在刚 delenv 之后恒真、抓不到任何
        # 东西，已删除。
        assert get_wiki_markdown_root() == get_markdown_root().parent / "3-Wiki"
        # 实测确认（不靠推理）：删掉 wiki env 后回落值仍落在 tmp_path 之下，
        # 夹具那条 `root.is_relative_to(tmp_path)` 守卫不受影响。
        assert get_wiki_markdown_root() == tmp_path / "3-Wiki"
        assert get_wiki_markdown_root().is_relative_to(tmp_path)

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

        # `own_path=target`：这篇无 `id:` 的剪藏笔记**就是本页记录在案的来源文件**
        # （`Page.external_id` 指向它），所以它算本页自己的、应当原地合并。
        # 不传 `own_path` 的含义是「本页没有记录在案的来源」——那时按 W2 的裁定要
        # **让开**（写到 `剪藏-<id8>.md`），不能覆盖用户手写的同名笔记。
        write_wiki_page_markdown(
            collection_name="C",
            collection_id="col-1",
            ancestors=[],
            page_id="p-1",
            name="剪藏",
            markdown="新正文",
            own_path=target,
        )
        text = target.read_text(encoding="utf-8")
        assert "  - 罗盘" in text
        assert "source: https://x" in text
        assert text.count("---") == 2
        assert text.endswith("新正文")
