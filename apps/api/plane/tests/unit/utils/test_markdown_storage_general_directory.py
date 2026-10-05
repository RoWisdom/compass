# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""「常规」分区在 wiki 树里的目录（罗盘 Round H）。

预置四分区（常规 / 私密 / 共享 / 已归档）**不建 `PageCollection` 行** ——
`utils/wiki_collections.py` 从页面自身字段推导归属（archived > private >
collection_id > general）。所以「常规」既没有名字也没有 id，不能靠
`wiki_collection_directory` 落成一个目录，只能有自己的一个常量 + 一行解析。

⚠️ 最容易写错的地方：想用 `wiki_page_markdown_path(collection_name="",
collection_id="")` 顶替。那会经 `wiki_collection_directory` 解析成
`root / ("" or "")`，而 pathlib 把 `root / ""` **折叠成 `root` 本身** —— 落点是
wiki 根，不是 `常规/`。第二条测试就是这条的回归锁。
"""

import os

import pytest

from plane.utils.markdown_storage import (
    GENERAL_DIRECTORY,
    wiki_general_directory,
    wiki_general_page_markdown_path,
    wiki_page_markdown_path,
    write_wiki_general_page_markdown,
)


@pytest.mark.unit
class TestWikiGeneralDirectory:
    def test_the_general_folder_sits_under_the_wiki_root(self, tmp_path):
        root = tmp_path / "3-Wiki"
        assert GENERAL_DIRECTORY == "常规"
        assert wiki_general_directory(root=root) == root / "常规"

    def test_the_path_never_collapses_to_the_wiki_root(self, tmp_path):
        """回归锁：空名的集合版会落到 **wiki 根**上，常规版必须落在 `常规/` 下。"""
        root = tmp_path / "3-Wiki"
        collapsed = wiki_page_markdown_path(
            collection_name="", collection_id="", ancestors=[], name="x", page_id="p1", root=root
        )
        assert collapsed == root / "x.md", "前置：集合版的空名退化确实落在根上"

        general = wiki_general_page_markdown_path(ancestors=[], name="x", page_id="p1", root=root)
        assert general == root / "常规" / "x.md"

    def test_sub_pages_nest_under_their_parent(self, tmp_path):
        """常规里可以有子页面（226 实测 10 行「collection 空且 parent 非空」）。"""
        root = tmp_path / "3-Wiki"
        path = wiki_general_page_markdown_path(
            ancestors=[("父页", "p-parent")], name="子页", page_id="p-child", root=root
        )
        assert path == root / "常规" / "父页" / "子页.md"

    def test_the_file_stem_falls_back_to_the_id(self, tmp_path):
        """无名页面（`name=None`）落成 id 当文件名 —— 与集合版同一个 `_file_stem`。"""
        root = tmp_path / "3-Wiki"
        path = wiki_general_page_markdown_path(ancestors=[], name=None, page_id="abcdef12-3456", root=root)
        assert path == root / "常规" / "abcdef12-3456.md"

    def test_write_lands_in_the_general_folder(self, tmp_path):
        root = tmp_path / "3-Wiki"
        write_wiki_general_page_markdown(ancestors=[], page_id="p1", name="x", markdown="正文", root=root)

        written = root / "常规" / "x.md"
        assert written.is_file()
        assert written.read_text(encoding="utf-8").endswith("正文")

    def test_a_read_only_root_does_not_raise(self, tmp_path, monkeypatch):
        """best-effort：只读盘不得让调用方（一次页面保存）失败。"""

        def _boom(*args, **kwargs):
            raise OSError("read-only vault")

        monkeypatch.setattr(os, "replace", _boom)

        write_wiki_general_page_markdown(
            ancestors=[], page_id="p1", name="x", markdown="正文", root=tmp_path / "3-Wiki"
        )
