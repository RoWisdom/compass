# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""wiki 侧的第二条落盘路径。

项目页面镜像与 wiki 页面镜像**各自**有一个根，二者按工作区独立配置
（`Workspace.project_markdown_path` / `wiki_markdown_path`），再往下才是环境变量
`MARKDOWN_STORAGE_PATH` / `WIKI_MARKDOWN_STORAGE_PATH`，最后是内置默认
`~/projects` / `~/wiki`。**wiki 根不再从 projects 根推导**（原先是
`get_markdown_root().parent / "3-Wiki"`）—— 那条耦合会让「改了项目目录、wiki 目录
跟着变」成为一个真实困惑。

测试隔离靠 conftest 那个 autouse 的 `isolate_markdown_mirror`，它**显式**给两个
环境变量都指到 tmp_path（故意的，曾挡住一次把镜像写进用户真实 vault 的事故，
不要动它）。"""

from pathlib import Path

import pytest

from plane.utils.markdown_storage import (
    DEFAULT_WIKI_MARKDOWN_PATH,
    MARKDOWN_STORAGE_PATH_ENV,
    WIKI_MARKDOWN_STORAGE_PATH_ENV,
    get_markdown_root,
    get_wiki_markdown_root,
    write_page_markdown,
    wiki_page_markdown_path,
    write_wiki_page_markdown,
)


@pytest.mark.unit
class TestGetWikiMarkdownRoot:
    def test_explicit_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(WIKI_MARKDOWN_STORAGE_PATH_ENV, str(tmp_path / "3-Wiki"))
        assert get_wiki_markdown_root() == tmp_path / "3-Wiki"

    def test_falls_back_to_the_builtin_default(self, monkeypatch):
        """真正走回落分支：两个 env 都没有时，wiki 根 = 内置默认 `~/wiki`。

        autouse 的 `isolate_markdown_mirror` 夹具**总是**设了 wiki env（钉住两个根是
        故意的，曾挡住一次把镜像写进用户真实 vault 的事故，不要动它）。所以这里必须由
        **测试自己**删掉那两个变量、再走回落——否则该函数永远走 env 分支，断言两侧
        都来自同一个 env 值，会退化成恒真的空转。
        """
        monkeypatch.delenv(MARKDOWN_STORAGE_PATH_ENV, raising=False)
        monkeypatch.delenv(WIKI_MARKDOWN_STORAGE_PATH_ENV, raising=False)

        assert get_wiki_markdown_root() == Path(DEFAULT_WIKI_MARKDOWN_PATH).expanduser()
        # 与 projects 根**没有**父子关系 —— 这条正是删掉推导的那个决定。
        assert get_wiki_markdown_root() != get_markdown_root().parent / "3-Wiki"

    def test_the_two_roots_are_independent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARKDOWN_STORAGE_PATH", str(tmp_path / "2-项目"))
        monkeypatch.setenv(WIKI_MARKDOWN_STORAGE_PATH_ENV, str(tmp_path / "别处"))
        assert get_wiki_markdown_root() == tmp_path / "别处"


@pytest.mark.unit
class TestWikiPageMarkdownPath:
    def test_top_level_page_sits_under_the_collection_folder(self, isolate_markdown_mirror):
        path = wiki_page_markdown_path(
            collection_name="Claude Code",
            collection_id="col-1",
            ancestors=[],
            name="安装与更新",
            page_id="page-1",
            root=get_wiki_markdown_root(),
        )
        assert path == isolate_markdown_mirror.parent / "3-Wiki" / "Claude Code" / "安装与更新.md"

    def test_collection_name_is_sanitized(self, isolate_markdown_mirror):
        path = wiki_page_markdown_path(
            collection_name="a/b:c",
            collection_id="col-1",
            ancestors=[],
            name="页",
            page_id="page-1",
            root=get_wiki_markdown_root(),
        )
        assert path.parent.name == "a-b-c"

    def test_missing_name_falls_back_to_the_id(self, isolate_markdown_mirror):
        path = wiki_page_markdown_path(
            collection_name="C",
            collection_id="col-1",
            ancestors=[],
            name="",
            page_id="page-1",
            root=get_wiki_markdown_root(),
        )
        assert path.name == "page-1.md"

    def test_ancestors_become_folders(self, isolate_markdown_mirror):
        path = wiki_page_markdown_path(
            collection_name="C",
            collection_id="col-1",
            ancestors=[("父页", "p-0")],
            name="子页",
            page_id="p-1",
            root=get_wiki_markdown_root(),
        )
        assert path.parent.name == "父页"
        assert path.name == "子页.md"

    def test_name_collision_with_a_different_page_does_not_overwrite(self, isolate_markdown_mirror, tmp_path):
        first = wiki_page_markdown_path(
            collection_name="C",
            collection_id="col-1",
            ancestors=[],
            name="同名",
            page_id="aaaaaaaa-1",
            root=get_wiki_markdown_root(),
        )
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("---\nid: bbbbbbbb-2\n---\n\n别人的正文\n", encoding="utf-8")
        second = wiki_page_markdown_path(
            collection_name="C",
            collection_id="col-1",
            ancestors=[],
            name="同名",
            page_id="cccccccc-3",
            root=get_wiki_markdown_root(),
        )
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
            root=get_wiki_markdown_root(),
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
            root=get_wiki_markdown_root(),
            own_path=target,
        )
        text = target.read_text(encoding="utf-8")
        assert "  - 罗盘" in text
        assert "source: https://x" in text
        assert text.count("---") == 2
        assert text.endswith("新正文")


@pytest.mark.unit
class TestProjectTreeLeavesAHandwrittenNoteAlone:
    def test_project_tree_leaves_a_handwritten_note_alone(self, isolate_markdown_mirror):
        """W2-2 的回归锁：项目树不传 `own_path`，目标名上那篇**没有 `id:`** 的手写笔记
        不是本页的 ⇒ 正文写到 `-{id8}` 兄弟文件上，原文件一个字节都不动。

        改动前这里的行为是**覆盖**。这条守的就是那次改动 —— 在此之前全仓没有一条
        项目树测试预置过无 `id:` 的同名文件，改回「覆盖」不会让任何测试变红。
        """
        target = get_markdown_root() / "面料交易" / "剪藏.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = "---\ntags:\n  - 罗盘\nsource: https://x\n---\n\n用户手写的正文\n"
        target.write_text(original, encoding="utf-8")

        write_page_markdown(
            project_name="面料交易",
            project_id="proj-1",
            ancestors=[],
            page_id="aaaaaaaa-1111-2222-3333-444444444444",
            name="剪藏",
            markdown="Plane 正文",
            root=get_markdown_root(),
        )

        assert target.read_text(encoding="utf-8") == original, "手写笔记必须逐字未动"
        sibling = target.parent / "剪藏-aaaaaaaa.md"
        assert sibling.is_file(), "正文要让到 -{id[:8]} 兄弟文件上"
        assert sibling.read_text(encoding="utf-8").endswith("Plane 正文")
