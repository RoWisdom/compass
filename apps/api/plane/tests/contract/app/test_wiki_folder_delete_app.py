# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""删文件夹（罗盘 Round I，设计 §4.1）。语义从 Round E 的「内容上浮一级」改为
**连里面的页面与子文件夹一起删**：

  · 整棵子树软删（`deleted_at` 落值），**文件夹行自己也软删**；
  · 同时属于某个项目的页面**一样软删**（设计 §2 I-3）—— 影响的是常规用法，
    因为「添加现有页面」的候选列表给的正是「我参与的项目」里的页面；
  · 磁盘上只删**我们自己写的**镜像（frontmatter `id:` 等于本页），
    导入原稿与别人的剪藏一律留下；目录只在空掉时 `rmdir`。

⚠️ 本文件与 `test_wiki_collection_delete_app.py` 是**同一套语义**的两个入口，
两边断言必须同步 —— 只改一边，另一边就是一份会「通过」的谎言。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, Project, ProjectPage, Workspace


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


def _deleted(page):
    page.refresh_from_db()
    return page.deleted_at is not None


@pytest.mark.contract
class TestTheWholeSubtreeIsSoftDeleted:
    @pytest.mark.django_db
    def test_every_descendant_gets_a_deleted_at(self, session_client, workspace, folder_tree):
        """删 B ⇒ B 的后代（t1 / C / t2）**每一行**都拿到 `deleted_at`。"""
        response = session_client.delete(_url(workspace, folder_tree["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for key in ("t1", "c", "t2"):
            assert _deleted(folder_tree[key]), f"{key} 应随整棵子树一起软删"

    @pytest.mark.django_db
    def test_the_folder_row_itself_is_deleted_too(self, session_client, workspace, folder_tree):
        """文件夹自己也是**软删**，不是 Round E 那种「只出 Wiki、行保留」。"""
        session_client.delete(_url(workspace, folder_tree["b"]))

        folder_tree["b"].refresh_from_db()
        assert folder_tree["b"].deleted_at is not None
        assert folder_tree["b"].is_global is False, "收录标记也该落下去"

    @pytest.mark.django_db
    def test_a_sibling_page_and_the_outside_page_are_untouched(self, session_client, workspace, folder_tree):
        session_client.delete(_url(workspace, folder_tree["b"]))

        for key in ("t3", "d", "outside"):
            assert not _deleted(folder_tree[key]), f"{key} 不在 B 的子树里，不得被删"

    @pytest.mark.django_db
    def test_deleting_a_top_level_folder_takes_its_whole_tree(self, session_client, workspace, folder_tree):
        session_client.delete(_url(workspace, folder_tree["a"]))

        for key in ("a", "b", "c", "d", "t1", "t2", "t3"):
            assert _deleted(folder_tree[key]), f"{key} 在 A 的子树里"
        assert not _deleted(folder_tree["outside"])

    @pytest.mark.django_db
    def test_an_empty_folder_can_be_deleted(self, session_client, workspace, folder_tree):
        response = session_client.delete(_url(workspace, folder_tree["d"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert _deleted(folder_tree["d"])

    @pytest.mark.django_db
    def test_the_deleted_rows_leave_the_wiki_endpoint(self, session_client, workspace, folder_tree):
        before = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?scope=all")
        assert before.status_code == status.HTTP_200_OK
        assert str(folder_tree["b"].id) in {row["id"] for row in before.data}

        session_client.delete(_url(workspace, folder_tree["b"]))

        after = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?scope=all")
        assert after.status_code == status.HTTP_200_OK
        ids_after = {row["id"] for row in after.data}
        for key in ("b", "t1", "c", "t2"):
            assert str(folder_tree[key].id) not in ids_after, f"{key} 不该再出现在树里"
        assert str(folder_tree["t3"].id) in ids_after, "兄弟节点要还在"


@pytest.mark.contract
class TestTheDualIdentityPageIsSoftDeletedToo:
    """设计 §2 I-3 的锁：同时属于某个项目的页面，**一样软删**。"""

    @pytest.mark.django_db
    def test_a_page_that_also_belongs_to_a_project_is_deleted(
        self, session_client, workspace, create_user, folder_tree
    ):
        project = Project.objects.create(name="项目", identifier="PRJ", workspace=workspace)
        ProjectPage.objects.create(
            workspace=workspace, project=project, page=folder_tree["t1"], created_by=create_user
        )

        response = session_client.delete(_url(workspace, folder_tree["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert _deleted(folder_tree["t1"]), "双身份页面也必须拿到 deleted_at"


@pytest.mark.contract
class TestDeletingAPageIsUnchanged:
    @pytest.mark.django_db
    def test_deleting_a_plain_page_still_only_un_includes_it(self, session_client, workspace, folder_tree):
        """页面行走**逐字不变**的老路径：出 Wiki、行**不删**、父不动。"""
        response = session_client.delete(_url(workspace, folder_tree["t3"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        folder_tree["t3"].refresh_from_db()
        assert folder_tree["t3"].deleted_at is None, "单页「移出 Wiki」不删行"
        assert folder_tree["t3"].is_global is False
        assert folder_tree["t3"].collection_id is None
        assert folder_tree["t3"].parent_id == folder_tree["a"].id

    @pytest.mark.django_db
    def test_deleting_a_page_does_not_touch_its_children(
        self, session_client, workspace, folder_tree, create_user
    ):
        """页面没有「上浮」语义 —— 它的子页留在原地当孤儿渲染（既有行为）。

        `t1` 在夹具里没有子节点，所以必须现给它挂一个：否则这条只验到 `is_global`，
        名字承诺的「不碰子节点」根本没被碰到。
        """
        child = _wiki_page(workspace, create_user, "t1 的子页", parent=folder_tree["t1"])

        session_client.delete(_url(workspace, folder_tree["t1"]))

        folder_tree["t1"].refresh_from_db()
        assert folder_tree["t1"].is_global is False
        assert folder_tree["t1"].deleted_at is None
        child.refresh_from_db()
        assert child.parent_id == folder_tree["t1"].id
        assert child.deleted_at is None


@pytest.mark.contract
class TestPermissions:
    @pytest.mark.django_db
    def test_a_folder_in_another_workspace_is_404(self, session_client, workspace, create_user):
        other = Workspace.objects.create(name="别的", slug="other-del-ws", owner=create_user)
        foreign = _folder(other, create_user, "外人")

        response = session_client.delete(f"/api/workspaces/{workspace.slug}/wiki-pages/{foreign.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        foreign.refresh_from_db()
        assert foreign.deleted_at is None
        assert foreign.is_global is True


@pytest.mark.contract
class TestDeletingAFolderDeletesTheMirrors:
    """镜像按设计 §4.3 收尾：只删我们自己写的、只删空目录。

    ⚠️ **这里的页面必须挂在一个真实集合里**。镜像是三分支路由（集合优先）：
    页面既没有 `collection_id`、又没有活着的 `ProjectPage` 链接时落「常规」，
    而本节的断言是按 `3-Wiki/<集合>/…` 写的 —— 用一棵带集合的树最不容易写错。
    """

    @pytest.fixture
    def scoped(self, workspace, create_user, isolate_markdown_mirror):
        """一棵**带集合**的小树，镜像已经落好：

            集合 C / 3-Wiki/C/
            └── A（文件夹）
                └── B（文件夹）
                    ├── t1（页面）    A/B/t1.md
                    └── C2（文件夹）
                        └── t2（页面） A/B/C2/t2.md
        """
        from plane.app.views.page.collection import _mirror_wiki_page

        c = PageCollection.objects.create(workspace=workspace, name="C", owned_by=create_user)
        a = _folder(workspace, create_user, "A", collection=c)
        b = _folder(workspace, create_user, "B", parent=a, collection=c)
        c2 = _folder(workspace, create_user, "C2", parent=b, collection=c)
        t1 = _wiki_page(workspace, create_user, "t1", parent=b, collection=c)
        t2 = _wiki_page(workspace, create_user, "t2", parent=c2, collection=c)
        for row in (t1, t2):
            _mirror_wiki_page(Page.objects.get(id=row.id), f"<p>{row.name} 的正文</p>")

        root = isolate_markdown_mirror.parent / "3-Wiki" / "C"
        assert (root / "A" / "B" / "t1.md").is_file(), "前置：t1 的镜像在 A/B/ 下"
        assert (root / "A" / "B" / "C2" / "t2.md").is_file(), "前置：t2 的镜像在 A/B/C2/ 下"
        return {"c": c, "a": a, "b": b, "c2": c2, "t1": t1, "t2": t2, "root": root}

    @pytest.mark.django_db
    def test_our_own_files_are_deleted(self, session_client, workspace, scoped):
        response = session_client.delete(_url(workspace, scoped["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not (scoped["root"] / "A" / "B" / "t1.md").exists(), "我们自己写的镜像要收掉"
        assert not (scoped["root"] / "A" / "B" / "C2" / "t2.md").exists(), "子文件夹里的也要收掉"

    @pytest.mark.django_db
    def test_the_emptied_folder_chain_is_removed_up_to_the_wiki_root(self, session_client, workspace, scoped):
        """一路往上爬：A/B 空了走、A 也跟着空了走、**集合目录**也空了于是走 —— 止步点是
        wiki 根（`_wiki_mirror_target` 返回的 `get_wiki_markdown_root`），它永不动。

        「删一个文件夹，把集合的目录也一起收掉了」看着像越界，其实是这条规则的自然结果，
        而且**无害**：目录只是按集合名拼出来的空壳，下次写页面时 `mkdir(parents=True)`
        会重建（`_write_page_file`）。真正不能碰的是**非空**目录 —— 下一条锁住它。
        """
        session_client.delete(_url(workspace, scoped["b"]))

        assert not (scoped["root"] / "A" / "B").exists(), "B 空了就该走"
        assert not (scoped["root"] / "A").exists(), "A 也跟着空了"
        assert not scoped["root"].exists(), "集合目录也空了 —— 止步点之上才是 wiki 根"
        assert scoped["root"].parent.is_dir(), "wiki 根永不动"

    @pytest.mark.django_db
    def test_a_file_that_is_not_ours_keeps_its_directory_alive(self, session_client, workspace, scoped):
        """设计 §2 I-4 的锁：目录里留着**不是我们写的**文件 ⇒ 文件一字不动、目录原样留下。"""
        original = scoped["root"] / "A" / "B" / "用户手写的.md"
        text = "---\ntags:\n  - 手写\n---\n\n这是我自己的笔记。\n"
        original.write_text(text, encoding="utf-8")

        session_client.delete(_url(workspace, scoped["b"]))

        assert original.read_text(encoding="utf-8") == text, "导入原稿/手写笔记一字不动"
        assert (scoped["root"] / "A" / "B").is_dir(), "非空目录必须原样留下"

    @pytest.mark.django_db
    def test_a_read_only_vault_does_not_fail_the_delete(self, session_client, workspace, scoped, monkeypatch):
        """best-effort：磁盘问题**永远不得**让一次删除失败（设计 §7 第一行）。"""
        import os

        def _boom(*args, **kwargs):
            raise OSError("read-only vault")

        monkeypatch.setattr(os, "unlink", _boom)

        response = session_client.delete(_url(workspace, scoped["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert _deleted(scoped["t1"]), "不只是 204：行确实被软删了"
        assert (scoped["root"] / "A" / "B" / "t1.md").is_file(), "删不掉就留着"
