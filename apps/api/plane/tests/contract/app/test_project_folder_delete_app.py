# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""删项目文件夹 = **整棵子树级联软删**（罗盘 Round J，裁定 D2）。

与 wiki 侧（`test_wiki_folder_delete_app.py`）是**同一套语义的两个入口**，
两边断言必须同步 —— 只改一边，另一边就是一份会「通过」的谎言。

⚠️ 两处**刻意**的不一致，不要「修」：

  · 删**文件夹**整棵连坐，删**页面**子页上浮（`test_project_page_folders_app.py` 的
    老路径逐字不变）。用户在被明确告知后选定 D2。
  · 删文件夹**不需要先归档**（裁定 甲），删单个页面**仍然需要**。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage, UserFavorite, UserRecentVisit


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
        session_client.delete(_url(workspace, project, folder_tree["b"]))

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

        after = session_client.get(base + "?scope=all")
        ids_after = {str(row["id"]) for row in after.data}
        for key in ("b", "t1", "c", "t2"):
            assert str(folder_tree[key].id) not in ids_after, f"{key} 不该再出现在树里"
        assert str(folder_tree["t3"].id) in ids_after, "兄弟节点要还在"


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
        session_client.delete(_url(workspace, project, folder_tree["b"]))

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
