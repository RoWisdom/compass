# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""删文件夹（罗盘 Round E，设计 §4.3）。语义照 Confluence Cloud：

  · 内容**不删**，整体**上浮一级**到被删文件夹的**原父级**；
  · 子文件夹连自己的整棵子树一起上浮；
  · 被删的文件夹自己出 Wiki（`is_global=False`），**行保留** —— 与既有 `destroy`
    对页面那条不变量同一条。

本文件专门盯住「上浮到**原父级**」而不是「上浮到集合顶层」—— 文件夹套文件夹时
两者结果不同，而渲染层的兜底（`wiki-tree.ts` 里「父不在集合里就当根」）只会给出后者。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, Workspace


def _wiki_page(workspace, user, name, **kwargs):
    return Page.objects.create(
        workspace=workspace,
        name=name,
        owned_by=user,
        access=Page.PUBLIC_ACCESS,
        is_global=True,
        description_html="<p></p>",
        description_json={},
        **kwargs,
    )


def _folder(workspace, user, name, **kwargs):
    kwargs.setdefault("node_type", Page.NODE_TYPE_FOLDER)
    return _wiki_page(workspace, user, name, **kwargs)


@pytest.fixture
def folder_tree(workspace, create_user):
    """形状照 `test_wiki_folder_app.py` 的 `folder_tree` 抄：

        A（文件夹）
        ├── B（文件夹）
        │   ├── t1（页面）
        │   └── C（文件夹）
        │       └── t2（页面）
        ├── t3（页面）
        └── D（空文件夹）
        outside（页面，与 A 完全无关）
    """
    a = _folder(workspace, create_user, "A")
    b = _folder(workspace, create_user, "B", parent=a)
    c = _folder(workspace, create_user, "C", parent=b)
    t1 = _wiki_page(workspace, create_user, "t1", parent=b)
    t2 = _wiki_page(workspace, create_user, "t2", parent=c)
    t3 = _wiki_page(workspace, create_user, "t3", parent=a)
    d = _folder(workspace, create_user, "D", parent=a)
    outside = _wiki_page(workspace, create_user, "outside")
    return {"a": a, "b": b, "c": c, "d": d, "t1": t1, "t2": t2, "t3": t3, "outside": outside}


def _url(workspace, page):
    return f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/"


@pytest.mark.contract
class TestTheContentsFloatUpOneLevel:
    @pytest.mark.django_db
    def test_direct_children_are_reparented_to_the_deleted_folders_parent(self, session_client, workspace, folder_tree):
        """删 B（B 的父是 A）⇒ B 的直接子节点（t1 与 C）挂到 **A** 下面，不是顶层。"""
        response = session_client.delete(_url(workspace, folder_tree["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for key in ("t1", "c"):
            folder_tree[key].refresh_from_db()
            assert folder_tree[key].parent_id == folder_tree["a"].id

    @pytest.mark.django_db
    def test_deeper_descendants_keep_their_own_parents(self, session_client, workspace, folder_tree):
        """t2 的父是 C、C 只是上浮 —— t2 不动，祖先链通过 C 传导。"""
        session_client.delete(_url(workspace, folder_tree["b"]))

        folder_tree["t2"].refresh_from_db()
        assert folder_tree["t2"].parent_id == folder_tree["c"].id

    @pytest.mark.django_db
    def test_deleting_a_top_level_folder_floats_children_to_the_collection_root(
        self, session_client, workspace, folder_tree
    ):
        """A 的父本来就是 None ⇒ 子节点也落到 None。"""
        session_client.delete(_url(workspace, folder_tree["a"]))

        for key in ("b", "t3", "d"):
            folder_tree[key].refresh_from_db()
            assert folder_tree[key].parent_id is None

    @pytest.mark.django_db
    def test_an_empty_folder_can_be_deleted(self, session_client, workspace, folder_tree):
        response = session_client.delete(_url(workspace, folder_tree["d"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        folder_tree["d"].refresh_from_db()
        assert folder_tree["d"].is_global is False


@pytest.mark.contract
class TestTheFolderItselfLeavesTheWiki:
    @pytest.mark.django_db
    def test_the_row_survives_but_leaves_the_wiki(self, session_client, workspace, folder_tree):
        session_client.delete(_url(workspace, folder_tree["b"]))

        folder_tree["b"].refresh_from_db()
        assert folder_tree["b"].is_global is False
        assert folder_tree["b"].collection_id is None

    @pytest.mark.django_db
    def test_the_children_stay_in_the_wiki(self, session_client, workspace, folder_tree):
        session_client.delete(_url(workspace, folder_tree["b"]))

        for key in ("t1", "c", "t2"):
            folder_tree[key].refresh_from_db()
            assert folder_tree[key].is_global is True

    @pytest.mark.django_db
    def test_the_deleted_folder_disappears_from_the_tree(self, session_client, workspace, folder_tree):
        before = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/")
        assert before.status_code == status.HTTP_200_OK
        assert str(folder_tree["b"].id) in {row["id"] for row in before.data}

        session_client.delete(_url(workspace, folder_tree["b"]))

        after = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/")
        ids_after = {row["id"] for row in after.data}
        assert str(folder_tree["b"].id) not in ids_after
        assert str(folder_tree["t1"].id) in ids_after


@pytest.mark.contract
class TestDeletingAPageIsUnchanged:
    @pytest.mark.django_db
    def test_deleting_a_plain_page_still_only_un_includes_it(self, session_client, workspace, folder_tree):
        """页面行走**逐字不变**的老路径：出 Wiki，**父不动**。"""
        response = session_client.delete(_url(workspace, folder_tree["t3"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        folder_tree["t3"].refresh_from_db()
        assert folder_tree["t3"].is_global is False
        assert folder_tree["t3"].collection_id is None
        assert folder_tree["t3"].parent_id == folder_tree["a"].id

    @pytest.mark.django_db
    def test_deleting_a_page_does_not_touch_its_children(self, session_client, workspace, folder_tree):
        """页面没有「上浮」语义 —— 它的子页留在原地当孤儿渲染（既有行为）。"""
        session_client.delete(_url(workspace, folder_tree["t1"]))

        folder_tree["t1"].refresh_from_db()
        assert folder_tree["t1"].is_global is False


@pytest.mark.contract
class TestPermissions:
    @pytest.mark.django_db
    def test_a_folder_in_another_workspace_is_404(self, session_client, workspace, create_user):
        other = Workspace.objects.create(name="别的", slug="other-del-ws", owner=create_user)
        foreign = _folder(other, create_user, "外人")

        response = session_client.delete(f"/api/workspaces/{workspace.slug}/wiki-pages/{foreign.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        foreign.refresh_from_db()
        assert foreign.is_global is True


@pytest.mark.contract
class TestDeletingAFolderMovesTheMirrors:
    """子节点的文件必须跟着上浮（设计 §4.3 的 ④）—— 它们是**纯 SQL 重挂**，
    磁盘上不会自己动。

    ⚠️ **这里的页面必须挂在一个真实集合里**。`_mirror_wiki_page` 是**三分支**路由
    （集合优先，见其 docstring）：页面既没有 `collection_id`、又没有活着的
    `ProjectPage` 链接时，它**根本不写镜像** —— 只 log 一条 warning 就跳过。上面那个
    `folder_tree` 夹具的页面**没有集合**，所以在这里复用它会得到「前置断言就先挂了」，
    而失败现象看起来像功能没做对。必须自建一棵带集合的树。
    """

    @pytest.fixture
    def scoped(self, workspace, create_user):
        """一棵**带集合**的小树：

            集合 C
            └── A（文件夹）
                └── B（文件夹）
                    ├── t1（页面）
                    └── C2（文件夹）
                        └── t2（页面）
        """
        c = PageCollection.objects.create(workspace=workspace, name="C", owned_by=create_user)
        a = _folder(workspace, create_user, "A", collection=c)
        b = _folder(workspace, create_user, "B", parent=a, collection=c)
        c2 = _folder(workspace, create_user, "C2", parent=b, collection=c)
        t1 = _wiki_page(workspace, create_user, "t1", parent=b, collection=c)
        t2 = _wiki_page(workspace, create_user, "t2", parent=c2, collection=c)
        return {"c": c, "a": a, "b": b, "c2": c2, "t1": t1, "t2": t2}

    @pytest.mark.django_db
    def test_a_pages_file_floats_up_out_of_the_deleted_folder(
        self, session_client, isolate_markdown_mirror, workspace, scoped
    ):
        from plane.app.views.page.collection import _mirror_wiki_page

        root = isolate_markdown_mirror.parent / "3-Wiki" / "C"
        _mirror_wiki_page(Page.objects.get(id=scoped["t1"].id), "<p>正文</p>")
        assert (root / "A" / "B" / "t1.md").is_file(), "前置：t1 的镜像在 A/B/ 下"

        response = session_client.delete(_url(workspace, scoped["b"]))
        assert response.status_code == status.HTTP_204_NO_CONTENT

        assert (root / "A" / "t1.md").is_file(), "上浮到 A 下面"
        # ⚠️ 断言**文件**不在了，**不**断言 `A/B` 这个目录消失 —— 见本节末尾那条说明。
        assert not (root / "A" / "B" / "t1.md").exists(), "旧位置不得留文件"

    @pytest.mark.django_db
    def test_a_nested_folder_takes_its_own_subtree_along(
        self, session_client, isolate_markdown_mirror, workspace, scoped
    ):
        """C2 是 B 的直接子节点，它的同名目录被整体搬走，住在里面的 t2 跟着走。"""
        from plane.app.views.page.collection import _mirror_wiki_page

        root = isolate_markdown_mirror.parent / "3-Wiki" / "C"
        _mirror_wiki_page(Page.objects.get(id=scoped["t2"].id), "<p>正文</p>")
        assert (root / "A" / "B" / "C2" / "t2.md").is_file(), "前置：t2 的镜像在 A/B/C2/ 下"

        response = session_client.delete(_url(workspace, scoped["b"]))
        assert response.status_code == status.HTTP_204_NO_CONTENT

        assert (root / "A" / "C2" / "t2.md").is_file(), "C2 整棵子树跟着上浮"
        # 同第一条：断文件，不断目录。
        assert not (root / "A" / "B" / "C2" / "t2.md").exists(), "旧位置不得留文件"
