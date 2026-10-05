# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""删除镜像文件的两条纪律（罗盘 Round I，设计 §4.3）。

  · `delete_page_file` —— **只删我们自己写的**（frontmatter `id:` 等于本页）。
    没有 `id:` 行的文件可能是这一页被导入时的原稿（用户自己写的）；`id:` 是别人的
    则是别人的剪藏 —— 两种都留下。这条判定不是新发明的：它就是 `_resolve_page_path`
    与 `_move_page_file` 已经在用的同一个 `_frontmatter_id`，只是方向从「拒绝写/拒绝搬」
    变成「拒绝删」。本仓写死的失败方向是 **never 'a destroyed note'**。

  · `prune_empty_directories` —— 只删**空**目录，自底向上。文件夹目录里可能躺着
    不是镜像的文件（手写笔记、附件），`rmdir` 只可能失败，**永不 `rmtree`**。
"""

import pytest

from plane.utils.markdown_storage import delete_page_file, prune_empty_directories

PAGE_ID = "11111111-2222-3333-4444-555555555555"


def _mirror(path, page_id, body="正文"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nid: {page_id}\ntitle: t\n---\n\n{body}\n", encoding="utf-8")
    return path


@pytest.mark.unit
class TestDeletePageFile:
    def test_a_file_whose_id_is_this_page_is_deleted(self, tmp_path):
        path = _mirror(tmp_path / "3-Wiki" / "C" / "页.md", PAGE_ID)

        assert delete_page_file(path, PAGE_ID) is True
        assert not path.exists()

    def test_a_file_with_no_id_line_is_left_alone(self, tmp_path):
        """没有 `id:` 行 ⇒ 可能是这一页被导入时的原稿。一字不动。"""
        path = tmp_path / "3-Wiki" / "C" / "页.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        original = "---\ntags:\n  - 手写\n---\n\n这是我自己写的笔记，Plane 不认识它。\n"
        path.write_text(original, encoding="utf-8")

        assert delete_page_file(path, PAGE_ID) is False
        assert path.read_text(encoding="utf-8") == original

    def test_a_file_carrying_another_pages_id_is_left_alone(self, tmp_path):
        path = _mirror(tmp_path / "3-Wiki" / "C" / "页.md", "另一个页面的-id")

        assert delete_page_file(path, PAGE_ID) is False
        assert path.exists()

    def test_a_missing_file_returns_false_and_does_not_raise(self, tmp_path):
        assert delete_page_file(tmp_path / "3-Wiki" / "C" / "没有这个.md", PAGE_ID) is False

    def test_a_non_utf8_file_does_not_raise(self, tmp_path):
        """`UnicodeDecodeError` **不是** `OSError`，`_frontmatter_id` 只兜后者 ——
        不在这儿兜住，一个非 UTF-8 的文件会让整次删除 500。
        """
        path = tmp_path / "3-Wiki" / "C" / "页.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe\x00\x01 not utf-8")

        assert delete_page_file(path, PAGE_ID) is False
        assert path.exists()


@pytest.mark.unit
class TestPruneEmptyDirectories:
    def test_an_emptied_chain_is_removed_up_to_the_stop_at(self, tmp_path):
        root = tmp_path / "3-Wiki"
        deep = root / "C" / "A" / "B"
        deep.mkdir(parents=True)
        # 传进去的那条路径**不需要真的存在** —— 调用方是在文件删完之后才收目录的。

        prune_empty_directories([deep / "t1.md"], stop_at=root)

        assert not (root / "C" / "A" / "B").exists(), "B 空了就该走"
        assert not (root / "C").exists(), "一路往上都空了，跟着走"
        assert root.is_dir(), "stop_at 永不动"

    def test_a_directory_holding_a_foreign_file_stays(self, tmp_path):
        """目录里留着一个没被删的文件 ⇒ `rmdir` 以 ENOTEMPTY 失败 ⇒ 目录原样留下，
        并且**不再往上爬**（祖先含它，必然也非空）。
        """
        root = tmp_path / "3-Wiki"
        deep = root / "C" / "A"
        deep.mkdir(parents=True)
        keep = deep / "用户手写的.md"
        keep.write_text("我的笔记。\n", encoding="utf-8")

        prune_empty_directories([deep / "t1.md"], stop_at=root)

        assert deep.is_dir()
        assert keep.read_text(encoding="utf-8") == "我的笔记。\n"
        assert (root / "C").is_dir()

    def test_stop_at_is_never_removed_even_when_it_is_empty(self, tmp_path):
        root = tmp_path / "3-Wiki"
        root.mkdir()

        prune_empty_directories([root / "孤儿.md"], stop_at=root)

        assert root.is_dir()

    def test_a_path_outside_the_root_does_not_climb_off_the_tree(self, tmp_path):
        """路径不属于 `stop_at` 这棵树时**立刻停**，绝不顺着父目录爬到 `/`。"""
        root = tmp_path / "3-Wiki"
        root.mkdir()
        outside = tmp_path / "别的地方"
        outside.mkdir()

        prune_empty_directories([outside / "x.md"], stop_at=root)

        assert outside.is_dir()
        assert root.is_dir()
