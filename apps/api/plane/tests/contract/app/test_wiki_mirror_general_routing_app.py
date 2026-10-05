# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""镜像路由的第三档：无集合、无项目 ⇒ `3-Wiki/常规/`（罗盘 Round H，设计 §4.2）。

本轮只**填空格**：另两档（有集合 / 无集合有项目）一个字节都不动 —— 那两条的回归锁
在 `test_wiki_mirror_routing_app.py` 里，本文件补上第三档，外加一条把
`_wiki_mirror_path` 的返回值也钉住的断言（搬移走的是它，不是写侧那条）。
"""

import pytest

from plane.db.models import Page, PageCollection


def _wiki_page(workspace, user, name, **kwargs):
    return Page.objects.create(
        workspace=workspace,
        name=name,
        description_html="<p>原文</p>",
        owned_by=user,
        is_global=True,
        **kwargs,
    )


@pytest.mark.contract
class TestTheGeneralSlot:
    @pytest.mark.django_db
    def test_a_page_with_neither_mirrors_into_the_general_folder(
        self, isolate_markdown_mirror, workspace, create_user
    ):
        from plane.app.views.page.collection import _mirror_wiki_page

        page = _wiki_page(workspace, create_user, "孤儿页")

        _mirror_wiki_page(Page.objects.get(id=page.id), "<p>正文</p>")

        written = isolate_markdown_mirror.parent / "3-Wiki" / "常规" / "孤儿页.md"
        assert written.is_file()
        assert written.read_text(encoding="utf-8").endswith("正文")

    @pytest.mark.django_db
    def test_the_move_resolves_the_general_path_instead_of_none(
        self, isolate_markdown_mirror, workspace, create_user
    ):
        """搬移读的是 `_wiki_mirror_path`。它返回 `None` 时 `_move_wiki_page_mirror`
        会把页面判成「没有家」而**原地不动**（`new_path is None` 分支）——
        删集合时那些页面的文件就会永远留在 `3-Wiki/<集合>/` 下面。
        """
        from plane.app.views.page.collection import _wiki_mirror_path

        page = _wiki_page(workspace, create_user, "孤儿页")

        path = _wiki_mirror_path(Page.objects.get(id=page.id), collection_id=None, name="孤儿页", ancestors=[])

        assert path is not None, "第三档不得再返回 None"
        assert path == isolate_markdown_mirror.parent / "3-Wiki" / "常规" / "孤儿页.md"

    @pytest.mark.django_db
    def test_a_sub_page_still_nests_under_its_parent(self, isolate_markdown_mirror, workspace, create_user):
        from plane.app.views.page.collection import _mirror_wiki_page

        parent = _wiki_page(workspace, create_user, "父页")
        child = _wiki_page(workspace, create_user, "子页", parent=parent)

        _mirror_wiki_page(Page.objects.get(id=child.id), "<p>正文</p>")

        assert (isolate_markdown_mirror.parent / "3-Wiki" / "常规" / "父页" / "子页.md").is_file()

    @pytest.mark.django_db
    def test_the_collection_slot_is_unchanged(self, isolate_markdown_mirror, workspace, create_user):
        from plane.app.views.page.collection import _mirror_wiki_page

        collection = PageCollection.objects.create(workspace=workspace, name="集合甲", owned_by=create_user)
        page = _wiki_page(workspace, create_user, "甲页", collection=collection)

        _mirror_wiki_page(Page.objects.get(id=page.id), "<p>正文</p>")

        assert (isolate_markdown_mirror.parent / "3-Wiki" / "集合甲" / "甲页.md").is_file()
        assert not (isolate_markdown_mirror.parent / "3-Wiki" / "常规").exists()
