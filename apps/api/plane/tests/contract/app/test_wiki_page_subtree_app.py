# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""移动父页 ⇒ 整棵子树跟着走（设计 B-4）。

裁定 6 明确接受了这条级联的代价：移动一个父页会连带移动用户**没有直接选中**的页面。
这里锁住它确实发生、确实**只在集合真变了时**发生、以及**不会跑到别的workspace去**。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, Workspace, WorkspaceMember


def _page(workspace, user, name, **kwargs):
    return Page.objects.create(
        name=name, workspace=workspace, owned_by=user, is_global=True,
        description_html="<p></p>", description_json={}, **kwargs,
    )


@pytest.fixture
def other_workspace(db, create_user):
    other = Workspace.objects.create(name="别的工作区", owner=create_user, slug="other-workspace")
    WorkspaceMember.objects.create(workspace=other, member=create_user, role=20)
    return other


@pytest.mark.contract
class TestWikiPageSubtreeCascade:
    @pytest.mark.django_db
    def test_moving_a_parent_moves_its_whole_subtree(self, session_client, workspace, create_user):
        parent = _page(workspace, create_user, "父")
        child = _page(workspace, create_user, "子", parent=parent)
        grandchild = _page(workspace, create_user, "孙", parent=child)
        collection = PageCollection.objects.create(workspace=workspace, name="目标集合", owned_by=create_user)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{parent.id}/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        for page in (parent, child, grandchild):
            page.refresh_from_db()
            assert page.collection_id == collection.id, f"{page.name} 没跟上"

    @pytest.mark.django_db
    def test_moving_a_page_back_to_general_moves_its_subtree(self, session_client, workspace, create_user):
        """`collection_id: null` 是「移回 general」，级联同样要发生 —— 不然子页会
        留在旧集合里，而父页已经不在了。"""
        collection = PageCollection.objects.create(workspace=workspace, name="旧集合", owned_by=create_user)
        parent = _page(workspace, create_user, "父", collection=collection)
        child = _page(workspace, create_user, "子", parent=parent, collection=collection)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{parent.id}/",
            {"collection_id": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        for page in (parent, child):
            page.refresh_from_db()
            assert page.collection_id is None

    @pytest.mark.django_db
    def test_a_patch_that_does_not_change_the_collection_moves_nothing(
        self, session_client, workspace, create_user
    ):
        """只改标题时**不该**走级联那一趟 —— 每次 PATCH 都遍历整棵子树是白烧。"""
        collection = PageCollection.objects.create(workspace=workspace, name="父页的集合", owned_by=create_user)
        parent = _page(workspace, create_user, "父", collection=collection)
        child = _page(workspace, create_user, "子", parent=parent)  # 刻意留在 general

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{parent.id}/",
            {"name": "改了名"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        child.refresh_from_db()
        assert child.collection_id is None, "改标题不该动子页的集合"

    @pytest.mark.django_db
    def test_descendants_in_another_workspace_are_not_touched(
        self, session_client, workspace, other_workspace, create_user
    ):
        """级联按 `workspace_id` 收窄 —— 绝不写别的工作区的行。

        正常写入路径不可能造出这种状态（建页时 workspace 与 parent 一起给），
        但级联是**写**操作，一个 `workspace_id` 过滤就是它的边界。
        """
        collection = PageCollection.objects.create(workspace=workspace, name="目标集合", owned_by=create_user)
        parent = _page(workspace, create_user, "父")
        foreign = _page(other_workspace, create_user, "别家的页", parent=parent)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{parent.id}/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        foreign.refresh_from_db()
        assert foreign.collection_id is None

    @pytest.mark.django_db
    def test_a_cyclic_parent_graph_terminates(self, session_client, workspace, create_user):
        """成环的 parent 图必须**终止**（设计 B-4 要求遍历有界 + visited 集）。

        B 的父是 A。Round E 起 wiki 的路由**可以**改 `parent`（`WikiPageUpdateSerializer` 有这个字段），
        但环仍能从数据层造出来（绕过 API 直接改库）—— 级联是**写**操作，撞上环会写成死循环。
        """
        a = _page(workspace, create_user, "A")
        b = _page(workspace, create_user, "B", parent=a)
        # 造环：A 的父指向 B。
        Page.objects.filter(id=a.id).update(parent=b)
        collection = PageCollection.objects.create(workspace=workspace, name="目标集合", owned_by=create_user)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{a.id}/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, "成环时必须返回，不能挂死"
        b.refresh_from_db()
        assert b.collection_id == collection.id
