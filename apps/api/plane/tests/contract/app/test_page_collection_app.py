# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, User


@pytest.fixture
def wiki_page(workspace, create_user):
    """一个已收录进 Wiki 的普通公开页面。"""
    return Page.objects.create(
        workspace=workspace,
        name="Wiki 页面一",
        owned_by=create_user,
        access=Page.PUBLIC_ACCESS,
        is_global=True,
    )


@pytest.fixture
def other_user(db):
    """本工作区里的第二个用户。

    User.username 是 unique=True，而 create_user fixture 建的第一个用户
    username 是 ""，所以这里必须显式给一个非空值。
    """
    return User.objects.create(email="other@plane.so", username="other-user")


@pytest.mark.contract
class TestPageCollectionEndpoint:
    @pytest.mark.django_db
    def test_list_returns_four_predefined_partitions(self, session_client, workspace):
        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        assert response.status_code == status.HTTP_200_OK
        keys = [item["key"] for item in response.data["predefined"]]
        assert keys == ["general", "private", "shared", "archived"]

    @pytest.mark.django_db
    def test_general_count_covers_included_public_pages(self, session_client, workspace, wiki_page):
        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        general = next(item for item in response.data["predefined"] if item["key"] == "general")
        assert general["page_count"] == 1

    @pytest.mark.django_db
    def test_pages_not_included_in_wiki_are_excluded(self, session_client, workspace, create_user):
        """is_global=False 的页面（即未收录的项目页面）不进任何分区。"""
        Page.objects.create(
            workspace=workspace, name="未收录", owned_by=create_user, access=Page.PUBLIC_ACCESS, is_global=False
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        general = next(item for item in response.data["predefined"] if item["key"] == "general")
        assert general["page_count"] == 0

    @pytest.mark.django_db
    def test_archived_page_lands_in_archived_not_general(self, session_client, workspace, create_user):
        Page.objects.create(
            workspace=workspace,
            name="已归档",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
            archived_at="2026-01-01",
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        by_key = {item["key"]: item["page_count"] for item in response.data["predefined"]}
        assert by_key["archived"] == 1
        assert by_key["general"] == 0

    @pytest.mark.django_db
    def test_private_page_lands_in_private(self, session_client, workspace, create_user):
        Page.objects.create(
            workspace=workspace, name="私有", owned_by=create_user, access=Page.PRIVATE_ACCESS, is_global=True
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        by_key = {item["key"]: item["page_count"] for item in response.data["predefined"]}
        assert by_key["private"] == 1
        assert by_key["general"] == 0

    @pytest.mark.django_db
    def test_private_count_hides_other_users_pages(self, session_client, workspace, create_user, other_user):
        """私有分区的计数必须与 /wiki-pages/ 列表同口径 —— 否则侧栏显示
        私有(2) 而列表只有 1 行。"""
        Page.objects.create(
            workspace=workspace, name="我的私有", owned_by=create_user, access=Page.PRIVATE_ACCESS, is_global=True
        )
        Page.objects.create(
            workspace=workspace, name="别人的私有", owned_by=other_user, access=Page.PRIVATE_ACCESS, is_global=True
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        by_key = {item["key"]: item["page_count"] for item in response.data["predefined"]}
        assert by_key["private"] == 1

    @pytest.mark.django_db
    def test_public_pages_of_other_users_are_still_counted(self, session_client, workspace, other_user):
        """别人的**公开**页面照旧计数 —— 可见性只对私有页收窄。

        `test_private_count_hides_other_users_pages` 只钉了收窄那一面：把 `_visible_page_q`
        写成 `Q(owned_by=request.user)`（只看自己的）它照样绿，而整个 Wiki 的计数会只剩
        自己的页面。这条是那半边唯一的钉子，与 `test_wiki_pages_app.py` 的
        `test_public_pages_of_other_users_are_still_visible` 同款、同口径。
        """
        Page.objects.create(
            workspace=workspace, name="同事的公开", owned_by=other_user, access=Page.PUBLIC_ACCESS, is_global=True
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        general = next(item for item in response.data["predefined"] if item["key"] == "general")
        assert general["page_count"] == 1

    @pytest.mark.django_db
    def test_shared_is_always_empty_in_oss(self, session_client, workspace, wiki_page):
        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        shared = next(item for item in response.data["predefined"] if item["key"] == "shared")
        assert shared["page_count"] == 0

    @pytest.mark.django_db
    def test_user_collections_are_listed_with_counts(self, session_client, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="设计文档", owned_by=create_user)
        Page.objects.create(
            workspace=workspace,
            name="设计一",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
            collection=collection,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        assert len(response.data["collections"]) == 1
        item = response.data["collections"][0]
        assert item["name"] == "设计文档"
        assert item["page_count"] == 1

    @pytest.mark.django_db
    def test_page_in_a_user_collection_leaves_general(self, session_client, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="设计文档", owned_by=create_user)
        Page.objects.create(
            workspace=workspace,
            name="设计一",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
            collection=collection,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        general = next(item for item in response.data["predefined"] if item["key"] == "general")
        assert general["page_count"] == 0

    @pytest.mark.django_db
    def test_another_workspace_is_not_visible(self, session_client, workspace, wiki_page):
        """跨工作区数据不得泄漏。"""
        from plane.db.models import User, Workspace, WorkspaceMember

        # username 必须显式给：create_user fixture 建的第一个用户 username 是 "",
        # 而 User.username 有 unique 约束，两个空字符串会撞。
        other_user = User.objects.create(email="other@plane.so", username="other-user")
        other_ws = Workspace.objects.create(name="Other", owner=other_user, slug="other-workspace")
        WorkspaceMember.objects.create(workspace=other_ws, member=other_user, role=20)
        Page.objects.create(
            workspace=other_ws, name="别家的", owned_by=other_user, access=Page.PUBLIC_ACCESS, is_global=True
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        general = next(item for item in response.data["predefined"] if item["key"] == "general")
        assert general["page_count"] == 1  # 只有本工作区的那一个
