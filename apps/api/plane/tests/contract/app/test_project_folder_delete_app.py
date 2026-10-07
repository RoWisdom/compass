# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""删项目文件夹 = **整棵子树级联软删**（罗盘 Round J，裁定 D2）。

与 wiki 侧（`test_wiki_folder_delete_app.py`）是**同一套语义的两个入口**：两边调的是
**同一份**级联实现（`app/views/page/cascade.py:_cascade_delete_pages`）。所以**级联
本身的**断言两边必须同步 —— 整棵子树软删、双身份页面连坐、镜像收尾这三条，只改一边，
另一边就是一份会「通过」的谎言。此外两份文件各有**这棵树专属**的断言，刻意没有对方
那一半（项目侧：`ProjectPage` through 行、收藏/最近访问、「单页仍需先归档」；wiki 侧：
集合路由）—— 那不是不同步，是两条路本来就不同。

⚠️ 两处**刻意**的不一致，不要「修」：

  · 删**文件夹**整棵连坐，删**页面**子页上浮（`test_project_page_folders_app.py` 的
    老路径逐字不变）。用户在被明确告知后选定 D2。
  · 删文件夹**不需要先归档**（裁定 甲），删单个页面**仍然需要**。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage, User, UserFavorite, UserRecentVisit


def _page(workspace, project, user, name, **kwargs):
    page = Page.objects.create(
        workspace=workspace,
        name=name,
        owned_by=user,
        access=Page.PUBLIC_ACCESS,
        description_html="<p></p>",
        description_json={},
        **kwargs,
    )
    ProjectPage.objects.create(workspace=workspace, project=project, page=page, created_by=user)
    return page


@pytest.fixture
def project(workspace, create_user):
    project = Project.objects.create(name="罗盘项目", identifier="LC", workspace=workspace, created_by=create_user)
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def folder_tree(workspace, project, create_user):
    """A（文件夹）├── B（文件夹）│ ├── t1（页）│ └── C（文件夹）│ └── t2（页）
    ├── t3（页）└── D（空文件夹）；outside（顶层页面，与 A 无关）。"""
    a = _page(workspace, project, create_user, "A", node_type=Page.NODE_TYPE_FOLDER)
    b = _page(workspace, project, create_user, "B", node_type=Page.NODE_TYPE_FOLDER, parent=a)
    c = _page(workspace, project, create_user, "C", node_type=Page.NODE_TYPE_FOLDER, parent=b)
    t1 = _page(workspace, project, create_user, "t1", parent=b)
    t2 = _page(workspace, project, create_user, "t2", parent=c)
    t3 = _page(workspace, project, create_user, "t3", parent=a)
    d = _page(workspace, project, create_user, "D", node_type=Page.NODE_TYPE_FOLDER, parent=a)
    outside = _page(workspace, project, create_user, "outside")
    return {"a": a, "b": b, "c": c, "d": d, "t1": t1, "t2": t2, "t3": t3, "outside": outside}


def _url(workspace, project, page):
    return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/"


def _deleted(page):
    page.refresh_from_db()
    return page.deleted_at is not None


@pytest.mark.contract
class TestTheWholeSubtreeIsSoftDeleted:
    @pytest.mark.django_db
    def test_every_descendant_gets_a_deleted_at(self, session_client, workspace, project, folder_tree):
        response = session_client.delete(_url(workspace, project, folder_tree["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for key in ("b", "t1", "c", "t2"):
            assert _deleted(folder_tree[key]), f"{key} 应随整棵子树一起软删"

    @pytest.mark.django_db
    def test_a_sibling_and_the_outside_page_are_untouched(self, session_client, workspace, project, folder_tree):
        response = session_client.delete(_url(workspace, project, folder_tree["b"]))

        # 正向锚：先钉住「这一次删除真的成功了、目标子树真的被删了」。没有它，这条
        # 就只断言邻居没被碰 —— 哪怕端点整个坏掉（对什么都返回 400），它照样过。
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert _deleted(folder_tree["t1"]), "先确认目标子树确实被删了"

        for key in ("t3", "d", "outside"):
            assert not _deleted(folder_tree[key]), f"{key} 不在 B 的子树里"

    @pytest.mark.django_db
    def test_deleting_a_top_level_folder_takes_its_whole_tree(self, session_client, workspace, project, folder_tree):
        session_client.delete(_url(workspace, project, folder_tree["a"]))

        for key in ("a", "b", "c", "d", "t1", "t2", "t3"):
            assert _deleted(folder_tree[key]), f"{key} 在 A 的子树里"
        assert not _deleted(folder_tree["outside"])

    @pytest.mark.django_db
    def test_an_empty_folder_can_be_deleted(self, session_client, workspace, project, folder_tree):
        response = session_client.delete(_url(workspace, project, folder_tree["d"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert _deleted(folder_tree["d"])

    @pytest.mark.django_db
    def test_the_deleted_rows_leave_the_tree_endpoint(self, session_client, workspace, project, folder_tree):
        base = f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/"

        before = session_client.get(base + "?scope=all")
        assert str(folder_tree["b"].id) in {str(row["id"]) for row in before.data}

        session_client.delete(_url(workspace, project, folder_tree["b"]))

        # 正向锚：这条用例的名字听着像在守级联，但它自己**只**守得住视图里那一刀
        # `ProjectPage` through 行清理。`?scope=all` 的过滤是 `project=Exists(
        # ProjectPage.objects.filter(...))`，through 行一软删，行就已经离开了树 ——
        # 哪怕 `_cascade_delete_pages` 被短路成 no-op、`Page.deleted_at` 从没落过值，
        # 本条依旧全绿。所以必须显式钉住「级联真的跑了」。
        assert _deleted(folder_tree["t1"]), "先确认级联真的跑了，而不只是 through 行被清"

        after = session_client.get(base + "?scope=all")
        ids_after = {str(row["id"]) for row in after.data}
        for key in ("b", "t1", "c", "t2"):
            assert str(folder_tree[key].id) not in ids_after, f"{key} 不该再出现在树里"
        assert str(folder_tree["t3"].id) in ids_after, "兄弟节点要还在"


@pytest.mark.contract
class TestTheDualIdentityPageIsSoftDeletedToo:
    """设计 §2 I-3 的锁：一个页面**既在项目文件夹里、又被收录进 Wiki**（双身份），
    删掉那个文件夹后，`is_global` 必须随级联一起落下（`is_global=False`）—— 这是
    规格里一条**显式约束**，本条就是在钉住它。

    真正钉死它的是下面那句直接的 `row.is_global is False`。本用例另一半的端到端检查
    （`wiki-pages/?scope=all`）**抓不到**这条的回归：wiki 读者查的是 `Page.objects`
    （`SoftDeletionManager`），`deleted_at` 一落值行就已被滤掉，哪怕 `is_global`
    忘了落，那一半照样绿 —— 端到端那一半守的是「读者看不见」，不是「标记落下了」。

    与 wiki 侧 `test_wiki_folder_delete_app.py::TestTheDualIdentityPageIsSoftDeletedToo`
    互为镜像：那边测「wiki 页面同时挂在项目上」，这边测「项目页面同时收录进 Wiki」，
    验的是同一份级联（`_cascade_delete_pages` 里那句 `is_global=False`）的同一件事。
    """

    @pytest.mark.django_db
    def test_the_whole_subtree_loses_is_global(self, session_client, workspace, project, folder_tree):
        subtree = ("b", "t1", "c", "t2")
        # 「收录进 Wiki」= `is_global=True`。项目页面默认不收录（`Page.is_global` 默认
        # False），所以这里显式打开；`outside` 也一起打开，用来证明「只碰子树」。
        Page.objects.filter(id__in=[folder_tree[key].id for key in (*subtree, "outside")]).update(is_global=True)

        wiki_url = f"/api/workspaces/{workspace.slug}/wiki-pages/?scope=all"
        before = session_client.get(wiki_url)
        assert before.status_code == status.HTTP_200_OK
        assert str(folder_tree["t1"].id) in {row["id"] for row in before.data}, "前置：双身份页面在 Wiki 里"

        response = session_client.delete(_url(workspace, project, folder_tree["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for key in subtree:
            # 走 `all_objects`：`deleted_at` 一落值，默认 `objects`（SoftDeletionManager）
            # 就再也查不到这一行了（与 `TestTheProjectPageRowsAreCleanedUp` 同一条纪律）。
            row = Page.all_objects.get(id=folder_tree[key].id)
            assert row.deleted_at is not None, f"{key} 应随整棵子树一起软删"
            assert row.is_global is False, f"{key} 的收录标记也要落下（规格 §2 I-3 的显式约束）"

        # 不在子树里的页面：行与收录标记都不得被碰。
        outside = Page.all_objects.get(id=folder_tree["outside"].id)
        assert outside.deleted_at is None
        assert outside.is_global is True

        # 端到端锚：Wiki 读者（同一个 `wiki-pages` 端点）再也看不到这棵子树。
        after = session_client.get(wiki_url)
        assert after.status_code == status.HTTP_200_OK
        ids_after = {row["id"] for row in after.data}
        for key in subtree:
            assert str(folder_tree[key].id) not in ids_after, f"{key} 不该再出现在 Wiki 里"
        assert str(folder_tree["outside"].id) in ids_after, "别人的收录页面要还在"


@pytest.mark.contract
class TestTheProjectPageRowsAreCleanedUp:
    """设计 §8.4 的锁：级联走的是**批量 update**，绕过 `SoftDeleteModel.delete()`，
    所以 `ProjectPage` through 行必须**在这里**补一刀。"""

    @pytest.mark.django_db
    def test_every_project_page_row_gets_a_deleted_at(self, session_client, workspace, project, folder_tree):
        session_client.delete(_url(workspace, project, folder_tree["b"]))

        for key in ("b", "t1", "c", "t2"):
            # 走 `all_objects`：`ProjectPage` 是 `BaseModel` → `AuditModel` → `SoftDeleteModel`，
            # 所以它的默认 `objects` 是 `SoftDeletionManager`（滤 `deleted_at__isnull=True`）。
            # 用 `all_objects` 才能断言「行还在、只是被软删」——这同时排除了「被硬删」。
            link = ProjectPage.all_objects.get(page_id=folder_tree[key].id)
            assert link.deleted_at is not None, f"{key} 的 ProjectPage 行也必须软删"

    @pytest.mark.django_db
    def test_outside_rows_keep_their_link(self, session_client, workspace, project, folder_tree):
        response = session_client.delete(_url(workspace, project, folder_tree["b"]))

        # 正向锚：先确认「项目侧补的那一刀」真的落下了（`ProjectPage` 行的软删不在
        # 共用级联里，是本层补的）。有了它，下方「外面的行没被碰」才不是「什么都没发生」。
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Page.all_objects.get(id=folder_tree["t1"].id).deleted_at is not None, "先确认目标子树确实被删了"
        assert ProjectPage.all_objects.get(page_id=folder_tree["t1"].id).deleted_at is not None

        assert ProjectPage.all_objects.get(page_id=folder_tree["outside"].id).deleted_at is None


@pytest.mark.contract
class TestFavoritesAndRecentVisitsAreCleanedUp:
    @pytest.mark.django_db
    def test_favorite_and_recent_visit_rows_of_the_subtree_are_gone(
        self, session_client, workspace, project, folder_tree, create_user
    ):
        UserFavorite.objects.create(
            workspace=workspace,
            project=project,
            user=create_user,
            entity_identifier=folder_tree["t1"].id,
            entity_type="page",
        )
        UserRecentVisit.objects.create(
            workspace=workspace,
            project=project,
            user=create_user,
            entity_identifier=folder_tree["t1"].id,
            entity_name="page",
        )

        session_client.delete(_url(workspace, project, folder_tree["b"]))

        assert not UserFavorite.objects.filter(entity_identifier=folder_tree["t1"].id).exists()
        assert not UserRecentVisit.objects.filter(entity_identifier=folder_tree["t1"].id).exists()


@pytest.mark.contract
class TestTheArchiveGuardIsExemptedOnlyForFolders:
    """裁定 甲：豁免**只**给文件夹，单个页面的护栏逐字不变。"""

    @pytest.mark.django_db
    def test_an_unarchived_folder_is_still_deletable(self, session_client, workspace, project, folder_tree):
        assert folder_tree["d"].archived_at is None

        response = session_client.delete(_url(workspace, project, folder_tree["d"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT

    @pytest.mark.django_db
    def test_an_unarchived_page_is_still_rejected(self, session_client, workspace, project, folder_tree):
        assert folder_tree["outside"].archived_at is None

        response = session_client.delete(_url(workspace, project, folder_tree["outside"]))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        folder_tree["outside"].refresh_from_db()
        assert folder_tree["outside"].deleted_at is None


@pytest.mark.contract
class TestDeletingAPageKeepsItsOldSemantics:
    """删**单个页面**逐字不变：仍需先归档；不碰任何子节点（不连坐）。"""

    @pytest.mark.django_db
    def test_an_archived_page_is_deleted_and_its_children_float_up(
        self, session_client, workspace, project, folder_tree, create_user
    ):
        child = _page(workspace, project, create_user, "t1 的子页", parent=folder_tree["t1"])
        folder_tree["t1"].archived_at = folder_tree["t1"].created_at
        folder_tree["t1"].save()

        response = session_client.delete(_url(workspace, project, folder_tree["t1"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert _deleted(folder_tree["t1"])
        child.refresh_from_db()
        assert child.deleted_at is None
        assert child.parent_id is None, "子页上浮到顶层 —— 与删文件夹的连坐刻意不同"


@pytest.mark.contract
class TestPermissions:
    @pytest.mark.django_db
    def test_a_folder_in_another_project_is_404(self, session_client, workspace, create_user, project):
        other = Project.objects.create(name="别的", identifier="OT", workspace=workspace, created_by=create_user)
        ProjectMember.objects.create(project=other, member=create_user, workspace=workspace, role=20)
        foreign = _page(workspace, other, create_user, "外人", node_type=Page.NODE_TYPE_FOLDER)

        response = session_client.delete(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{foreign.id}/"
        )

        assert response.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
        foreign.refresh_from_db()
        assert foreign.deleted_at is None

    @pytest.mark.django_db
    def test_a_member_who_owns_nothing_cannot_delete_a_folder(
        self, session_client, workspace, project, create_user, folder_tree
    ):
        """端到端结果锁：非属主、非 admin 的成员删文件夹必须 403，且整棵子树一行都不许动。

        这个 403 由 **DRF 的 `ProjectPagePermission`** 落下 —— 它只放 ADMIN 过 DELETE
        （`app/permissions/page.py:121-124`），所以非 admin 根本进不到视图体；把视图里
        那句属主/admin 谓词短路成 `return True`，本条依旧 403（reviewer 实测）。
        视图层那道谓词是**第二道闸，在 DELETE 上当前够不到** —— 能过 DRF 的人要么是
        属主、要么是 ADMIN，两种都让谓词返回 True。所以本条守的是「结果」，
        不是「哪一层拦的」。

        `session_client` 认证为 `create_user`，这里把两条路都堵死：文件夹的属主换成
        **另一个人**，并把 `create_user` 从 ADMIN 降到 MEMBER（15；20 才是 ADMIN）。
        """
        owner = User.objects.create(
            email="folder-owner@plane.so",
            username="folder-owner",
            first_name="Folder",
            last_name="Owner",
        )
        Page.objects.filter(id=folder_tree["b"].id).update(owned_by=owner)
        ProjectMember.objects.filter(project=project, member=create_user).update(role=15)

        response = session_client.delete(_url(workspace, project, folder_tree["b"]))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        # 什么都不许动：文件夹自己、整棵子树、以及项目侧的 through 行。
        for key in ("b", "t1", "c", "t2"):
            assert not _deleted(folder_tree[key]), f"{key} 不得被删"
        assert ProjectPage.all_objects.get(page_id=folder_tree["b"].id).deleted_at is None


@pytest.mark.contract
class TestDeletingAFolderDeletesTheMirrors:
    """镜像按设计 §4.3 收尾（**项目侧**那条 `resolve_mirror`）：只删我们自己写的、只删空目录。

    与 wiki 侧 `test_wiki_folder_delete_app.py::TestDeletingAFolderDeletesTheMirrors`
    互为镜像 —— 两边跑的是同一份 `_cascade_delete_pages`，只是寻址函数换成项目树
    （`_project_mirror_target_resolver`）。所以断言逐条对应：我们的文件被收走、
    空掉的目录链一路爬到镜像根、别人的文件与目录留下、只读盘不挡删除。
    """

    @pytest.fixture
    def scoped(self, workspace, project, create_user, isolate_markdown_mirror):
        """一棵**镜像已经落好**的项目小树：

            项目目录 <项目镜像根>/<项目 id>/
            └── A（文件夹）
                └── B（文件夹）
                    ├── t1（页面）    A/B/t1.md
                    └── C2（文件夹）
                        └── t2（页面） A/B/C2/t2.md

        镜像用**生产侧同一个写盘函数**落下去（`PageViewSet.create` 调的那个
        `_write_page_mirror`），祖先链也走同一个 `_page_ancestors` —— 路径与
        frontmatter 里的 `id:` 与真实写入逐字同源，断言才不会对着一个我自己编的
        路径自说自话。
        """
        from plane.app.views.page.base import _page_ancestors, _project_mirror_root, _write_page_mirror
        from plane.utils.markdown_storage import _project_directory_name

        a = _page(workspace, project, create_user, "A", node_type=Page.NODE_TYPE_FOLDER)
        b = _page(workspace, project, create_user, "B", node_type=Page.NODE_TYPE_FOLDER, parent=a)
        c2 = _page(workspace, project, create_user, "C2", node_type=Page.NODE_TYPE_FOLDER, parent=b)
        t1 = _page(workspace, project, create_user, "t1", parent=b)
        t2 = _page(workspace, project, create_user, "t2", parent=c2)
        for row in (t1, t2):
            _write_page_mirror(
                project.id,
                row.id,
                row.name,
                _page_ancestors(row.parent_id),
                f"<p>{row.name} 的正文</p>",
            )

        mirror_root = _project_mirror_root(project.id)
        root = mirror_root / _project_directory_name(str(project.id))
        assert (root / "A" / "B" / "t1.md").is_file(), "前置：t1 的镜像在 A/B/ 下"
        assert (root / "A" / "B" / "C2" / "t2.md").is_file(), "前置：t2 的镜像在 A/B/C2/ 下"
        return {"a": a, "b": b, "c2": c2, "t1": t1, "t2": t2, "root": root, "mirror_root": mirror_root}

    @pytest.mark.django_db
    def test_our_own_files_are_deleted(self, session_client, workspace, project, scoped):
        response = session_client.delete(_url(workspace, project, scoped["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not (scoped["root"] / "A" / "B" / "t1.md").exists(), "我们自己写的镜像要收掉"
        assert not (scoped["root"] / "A" / "B" / "C2" / "t2.md").exists(), "子文件夹里的也要收掉"

    @pytest.mark.django_db
    def test_the_emptied_folder_chain_is_removed_up_to_the_mirror_root(
        self, session_client, workspace, project, scoped
    ):
        """一路往上爬：A/B 空了走、A 也跟着空了走、**项目目录**也空了于是走 —— 止步点是
        项目侧的镜像根（`get_markdown_root`，正是 `_project_mirror_target_resolver`
        返回的那个 `root`），它永不动。

        「删一个文件夹，把项目目录也一起收掉了」看着像越界，其实是这条规则的自然结果，
        而且**无害**：目录只是按项目 id 拼出来的空壳，下次写页面时 `mkdir(parents=True)`
        会重建（`_write_page_file`）。真正不能碰的是**非空**目录 —— 下一条锁住它。
        """
        session_client.delete(_url(workspace, project, scoped["b"]))

        assert not (scoped["root"] / "A" / "B").exists(), "B 空了就该走"
        assert not (scoped["root"] / "A").exists(), "A 也跟着空了"
        assert not scoped["root"].exists(), "项目目录也空了 —— 止步点之上才是镜像根"
        assert scoped["mirror_root"].is_dir(), "镜像根永不动"

    @pytest.mark.django_db
    def test_a_file_that_is_not_ours_keeps_its_directory_alive(self, session_client, workspace, project, scoped):
        """设计 §2 I-4 的锁：目录里留着**不是我们写的**文件 ⇒ 文件一字不动、目录原样留下。"""
        original = scoped["root"] / "A" / "B" / "用户手写的.md"
        text = "---\ntags:\n  - 手写\n---\n\n这是我自己的笔记。\n"
        original.write_text(text, encoding="utf-8")

        session_client.delete(_url(workspace, project, scoped["b"]))

        assert original.read_text(encoding="utf-8") == text, "导入原稿/手写笔记一字不动"
        assert (scoped["root"] / "A" / "B").is_dir(), "非空目录必须原样留下"

    @pytest.mark.django_db
    def test_a_read_only_vault_does_not_fail_the_delete(self, session_client, workspace, project, scoped, monkeypatch):
        """best-effort：磁盘问题**永远不得**让一次删除失败（设计 §7 第一行）。"""
        import os

        def _boom(*args, **kwargs):
            raise OSError("read-only vault")

        monkeypatch.setattr(os, "unlink", _boom)

        response = session_client.delete(_url(workspace, project, scoped["b"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert _deleted(scoped["t1"]), "不只是 204：行确实被软删了"
        assert (scoped["root"] / "A" / "B" / "t1.md").is_file(), "删不掉就留着"
