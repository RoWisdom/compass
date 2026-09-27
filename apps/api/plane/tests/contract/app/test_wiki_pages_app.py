# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import date, timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Page,
    PageCollection,
    Project,
    ProjectMember,
    ProjectPage,
    User,
    Workspace,
    WorkspaceMember,
)


@pytest.fixture
def project_page(workspace, create_user):
    """一个普通的、未收录的项目页面。"""
    return Page.objects.create(
        workspace=workspace, name="项目里的页面", owned_by=create_user, access=Page.PUBLIC_ACCESS
    )


@pytest.fixture
def other_user(db):
    """本工作区里的第二个用户。

    User.username 是 unique=True，而 create_user fixture 建的第一个用户
    username 是 ""，所以这里必须显式给一个非空值，否则 fixture setup 就撞
    IntegrityError。
    """
    return User.objects.create(email="other@plane.so", username="other-user")


@pytest.fixture
def other_workspace(db, other_user):
    """另一个工作区（连同它的成员关系），用来验证跨工作区隔离。"""
    workspace = Workspace.objects.create(name="Other", owner=other_user, slug="other-workspace")
    WorkspaceMember.objects.create(workspace=workspace, member=other_user, role=20)
    return workspace


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
def other_workspace_wiki_page(other_workspace):
    """别的工作区里一个同样已收录的页面 —— 收录标记不跨工作区。"""
    return Page.objects.create(
        workspace=other_workspace,
        name="别家的 Wiki 页面",
        owned_by=other_workspace.owner,
        access=Page.PUBLIC_ACCESS,
        is_global=True,
    )


@pytest.fixture
def guest(db, workspace):
    """本工作区里的一个只读成员（GUEST, role=5）。

    `plane/tests/conftest.py` 里**没有** guest fixture；形状照现成的先例抄 ——
    `plane/tests/contract/app/test_issue_list_guest_scope_app.py:50-66`。
    `User.username` 是 unique=True 且 create_user fixture 已占用了 ""，所以必须给唯一值。
    """
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"guest-{unique_id}@plane.so",
        username=f"guest_{unique_id}",
        first_name="Guest",
        last_name="User",
    )
    user.set_password("test-password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=5)
    return user


@pytest.fixture
def guest_client(guest):
    """以 GUEST 身份认证的客户端。

    **不能复用 session_client** —— 它已经 force_authenticate 成 create_user 了，
    再认证一次只会换掉身份、把「GUEST 被拒」测成「create_user 被拒」。
    """
    client = APIClient()
    client.force_authenticate(user=guest)
    return client


@pytest.mark.contract
class TestWikiPageList:
    @pytest.mark.django_db
    def test_lists_only_included_pages(self, session_client, workspace, project_page):
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?collection=general")
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    @pytest.mark.django_db
    def test_filters_by_partition(self, session_client, workspace, create_user):
        Page.objects.create(
            workspace=workspace, name="公开", owned_by=create_user, access=Page.PUBLIC_ACCESS, is_global=True
        )
        Page.objects.create(
            workspace=workspace, name="私有", owned_by=create_user, access=Page.PRIVATE_ACCESS, is_global=True
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?collection=private")

        assert response.status_code == status.HTTP_200_OK
        assert [page["name"] for page in response.data] == ["私有"]

    @pytest.mark.django_db
    def test_private_partition_hides_other_users_pages(self, session_client, workspace, create_user, other_user):
        """私有 = 只看自己的：别人的私有页面不得出现在我的 private 分区里。"""
        Page.objects.create(
            workspace=workspace, name="我的私有", owned_by=create_user, access=Page.PRIVATE_ACCESS, is_global=True
        )
        Page.objects.create(
            workspace=workspace, name="别人的私有", owned_by=other_user, access=Page.PRIVATE_ACCESS, is_global=True
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?collection=private")

        assert response.status_code == status.HTTP_200_OK
        assert [page["name"] for page in response.data] == ["我的私有"]

    @pytest.mark.django_db
    def test_filters_by_user_collection(self, session_client, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="设计", owned_by=create_user)
        Page.objects.create(
            workspace=workspace,
            name="设计一",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
            collection=collection,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?collection={collection.id}")

        assert [page["name"] for page in response.data] == ["设计一"]

    @pytest.mark.django_db
    def test_public_pages_of_other_users_are_still_visible(self, session_client, workspace, create_user, other_user):
        """可见性只对私有页面收窄 —— 别人的公开页面必须照旧出现在 general。

        把 ``_visible_page_q`` 写成 ``Q(owned_by=request.user)``（即"只看自己的"）
        会让整个 Wiki 里只剩自己的页面，而上面所有夹具页面都是 create_user 的，
        现有测试会全绿。这条是那种回归唯一的钉子。
        """
        Page.objects.create(
            workspace=workspace, name="我的公开", owned_by=create_user, access=Page.PUBLIC_ACCESS, is_global=True
        )
        Page.objects.create(
            workspace=workspace, name="别人的公开", owned_by=other_user, access=Page.PUBLIC_ACCESS, is_global=True
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?collection=general")

        assert response.status_code == status.HTTP_200_OK
        assert sorted(page["name"] for page in response.data) == ["别人的公开", "我的公开"]

    @pytest.mark.django_db
    def test_row_exposes_collection_id(self, session_client, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="设计", owned_by=create_user)
        Page.objects.create(
            workspace=workspace,
            name="设计一",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
            collection=collection,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?collection={collection.id}")

        assert response.data[0]["collection_id"] == str(collection.id)


@pytest.mark.contract
class TestWikiPageInclude:
    @pytest.mark.django_db
    def test_including_sets_is_global(self, session_client, workspace, project_page):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(project_page.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["included"] == 1
        project_page.refresh_from_db()
        assert project_page.is_global is True

    @pytest.mark.django_db
    def test_including_into_a_collection(self, session_client, workspace, create_user, project_page):
        collection = PageCollection.objects.create(workspace=workspace, name="设计", owned_by=create_user)

        session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(project_page.id)], "collection_id": str(collection.id)},
            format="json",
        )

        project_page.refresh_from_db()
        assert project_page.collection_id == collection.id

    @pytest.mark.django_db
    def test_including_is_idempotent(self, session_client, workspace, project_page):
        payload = {"page_ids": [str(project_page.id)]}
        first = session_client.post(f"/api/workspaces/{workspace.slug}/wiki-pages/", payload, format="json")
        second = session_client.post(f"/api/workspaces/{workspace.slug}/wiki-pages/", payload, format="json")

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_cannot_include_a_page_from_another_workspace(self, session_client, workspace, create_user):
        from plane.db.models import User, Workspace, WorkspaceMember

        # User.username 是 unique=True；create_user fixture 已占用了 ""，必须另给一个非空值
        other_user = User.objects.create(email="other@plane.so", username="other-user")
        other_ws = Workspace.objects.create(name="Other", owner=other_user, slug="other-workspace")
        WorkspaceMember.objects.create(workspace=other_ws, member=other_user, role=20)
        foreign_page = Page.objects.create(
            workspace=other_ws, name="别家的", owned_by=other_user, access=Page.PUBLIC_ACCESS
        )

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(foreign_page.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["included"] == 0
        foreign_page.refresh_from_db()
        assert foreign_page.is_global is False

    @pytest.mark.django_db
    def test_cannot_include_another_users_private_page(self, session_client, workspace, other_user):
        """别人的私有页面不能被收录 —— 与跨工作区一样静默跳过，不是 403。"""
        foreign_private = Page.objects.create(
            workspace=workspace, name="别人的私有", owned_by=other_user, access=Page.PRIVATE_ACCESS
        )

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(foreign_private.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["included"] == 0
        foreign_private.refresh_from_db()
        assert foreign_private.is_global is False

    @pytest.mark.django_db
    def test_cannot_include_into_another_workspaces_collection(
        self, session_client, workspace, other_workspace, project_page
    ):
        """跨工作区的集合 id 必须 404，且页面保持原样（is_global / collection 不变）。"""
        foreign_collection = PageCollection.objects.create(
            workspace=other_workspace, name="别家的集合", owned_by=other_workspace.owner
        )

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(project_page.id)], "collection_id": str(foreign_collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Collection not found."
        project_page.refresh_from_db()
        assert project_page.is_global is False
        assert project_page.collection_id is None

    @pytest.mark.django_db
    def test_nonexistent_collection_is_rejected(self, session_client, workspace, project_page):
        """不存在的集合 id 必须 404 —— 直接写进 FK 会 IntegrityError，用户输入换来 500。"""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(project_page.id)], "collection_id": str(uuid4())},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Collection not found."
        project_page.refresh_from_db()
        assert project_page.is_global is False
        assert project_page.collection_id is None

    @pytest.mark.django_db
    def test_empty_page_ids_is_rejected(self, session_client, workspace):
        response = session_client.post(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"page_ids": []}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.django_db
    def test_partition_key_is_rejected_as_collection_id(self, session_client, workspace, project_page):
        """`collection_id` 送的是**分区键**（"general"）时必须 400，且页面原样不动。

        这正是那轮 Critical 的确切失败模式：前端把分区键当 uuid 送出，UUIDField 报
        "Must be a valid UUID."。此前**没有任何一条测试**往 collection_id 里送过非 uuid
        字符串，所以这条路径一直没有钉子。
        页面原样那一半不能省 —— 只断言 400 对一个「先写后校验」的实现同样成立。
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(project_page.id)], "collection_id": "general"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "collection_id" in response.data
        project_page.refresh_from_db()
        assert project_page.is_global is False
        assert project_page.collection_id is None


@pytest.mark.contract
class TestWikiPageUpdateAndRemove:
    @pytest.mark.django_db
    def test_moving_a_page_between_collections(self, session_client, workspace, create_user):
        source = PageCollection.objects.create(workspace=workspace, name="源", owned_by=create_user)
        target = PageCollection.objects.create(workspace=workspace, name="目标", owned_by=create_user)
        page = Page.objects.create(
            workspace=workspace,
            name="页",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
            collection=source,
        )

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": str(target.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.collection_id == target.id

    @pytest.mark.django_db
    def test_moving_to_null_returns_the_page_to_general(self, session_client, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="设计", owned_by=create_user)
        page = Page.objects.create(
            workspace=workspace,
            name="页",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
            collection=collection,
        )

        session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": None},
            format="json",
        )

        page.refresh_from_db()
        assert page.collection_id is None
        assert page.is_global is True

    @pytest.mark.django_db
    def test_removing_from_wiki_does_not_delete_the_page(self, session_client, workspace, create_user):
        page = Page.objects.create(
            workspace=workspace, name="页", owned_by=create_user, access=Page.PUBLIC_ACCESS, is_global=True
        )

        response = session_client.delete(f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        page.refresh_from_db()
        assert page.is_global is False
        assert page.deleted_at is None  # 页面本身必须还在

    @pytest.mark.django_db
    def test_removing_also_clears_the_collection(self, session_client, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="设计", owned_by=create_user)
        page = Page.objects.create(
            workspace=workspace,
            name="页",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
            collection=collection,
        )

        session_client.delete(f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/")

        page.refresh_from_db()
        assert page.collection_id is None

    @pytest.mark.django_db
    def test_removing_another_users_private_page_is_a_404(self, session_client, workspace, other_user):
        """非所有者移出他人私有页 → 404（不是 403、更不是 204），页面保持原样。

        作用域继承自共享的 `_wiki_page_queryset`：私有页在 `_visible_page_q` 那一处就
        被滤掉了，所以这里看见的是「不存在」而不是「没权限」。正向那一面在
        `test_removing_from_wiki_does_not_delete_the_page`（自己的页 204）。
        """
        private_page = Page.objects.create(
            workspace=workspace, name="别人的私有", owned_by=other_user, access=Page.PRIVATE_ACCESS, is_global=True
        )

        response = session_client.delete(f"/api/workspaces/{workspace.slug}/wiki-pages/{private_page.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        private_page.refresh_from_db()
        assert private_page.is_global is True

    @pytest.mark.django_db
    def test_including_and_removing_refresh_updated_at(self, session_client, workspace, create_user, other_user):
        """收录/移出必须刷新 updated_at（QuerySet.update() 绕过 auto_now），
        否则列表默认排序键 -updated_at 不动；同时**不得**把收录者盖到 updated_by 上。

        `Page.updated_at` 是 `auto_now=True`（`db/mixins.py:20`），而收录与移出都走
        `QuerySet.update()` —— 它**绕过** auto_now，不显式传 updated_at 就一动不动。
        两条路径各钉一次：`create` 与 `destroy` 各有一处 update()，少传哪一处，
        对应那半条断言就红。

        先把 updated_at 按到 30 天前（同一条绕过 auto_now 的 update()），
        这样断言 `>` 有确定的比较基准，不依赖两次调用之间的挂钟差。

        另一半是 `updated_by`：收录只是把页面**标记**进 Wiki，不是一次内容编辑，
        所以审计位必须保持不动（用户裁定：只写 updated_at，不改「最后编辑者」）。
        `QuerySet.update()` 同样绕过 auto_now，但**不会**自己写 updated_by ——
        除非显式传 `updated_by=request.user`。

        基线刻意取**非空且不是 create_user** 的 `other_user`：加回
        `updated_by=request.user` 会让它变成 create_user，与 `None` 基线相比更结实
        （None 基线只是「不变」与「变成 create_user」之别，非空基线还排除了
        「顺带写成 None」这种改法）。

        基线本身必须走 `QuerySet.update()`：`BaseModel.save()` 在没有 request 的线程里
        会把 created_by/updated_by 一律置 None（`db/models/base.py:31-33`），
        所以 `Page.objects.create(updated_by=...)` 是**存不下来**的（实测 in-memory 就是
        None），只有 update() 这条路能立起非空基线。
        """
        page = Page.objects.create(
            workspace=workspace, name="页", owned_by=create_user, access=Page.PUBLIC_ACCESS, is_global=False
        )
        stale = timezone.now() - timedelta(days=30)

        Page.objects.filter(id=page.id).update(updated_at=stale, updated_by=other_user)
        session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(page.id)]},
            format="json",
        )
        page.refresh_from_db()
        assert page.is_global is True
        assert page.updated_at > stale
        assert page.updated_by_id == other_user.id

        Page.objects.filter(id=page.id).update(updated_at=stale)
        session_client.delete(f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/")
        page.refresh_from_db()
        assert page.is_global is False
        assert page.updated_at > stale
        assert page.updated_by_id == other_user.id


@pytest.mark.contract
class TestWikiPageDetailEndpoint:
    @pytest.mark.django_db
    def test_retrieve_returns_description_html(self, session_client, workspace, wiki_page):
        wiki_page.description_html = "<p>正文</p>"
        wiki_page.description_json = {"type": "doc"}
        wiki_page.save()

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(wiki_page.id)
        assert response.data["description_html"] == "<p>正文</p>"
        assert response.data["description_json"] == {"type": "doc"}

    @pytest.mark.django_db
    def test_retrieve_404_for_page_not_in_wiki(self, session_client, workspace, create_user):
        """未收录（is_global=False）的页面不属于 Wiki，取不到。"""
        page = Page.objects.create(
            workspace=workspace,
            name="未收录",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_retrieve_404_across_workspaces(self, session_client, other_workspace_wiki_page, workspace):
        """别的工作区的页面，即使已收录，也不能通过本工作区读到。"""
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{other_workspace_wiki_page.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_retrieve_404_for_another_users_private_page(self, session_client, workspace, other_user):
        """别人的私有页面连正文都取不到 —— 详情页是唯一会吐出正文的读路径。

        作用域来自共享的 _wiki_page_queryset，这里只钉住它确实被继承到：
        将来谁把 _visible_page_q 挪出共享助手、只留给 list，这条就会红。
        """
        private_page = Page.objects.create(
            workspace=workspace,
            name="别人的私有",
            owned_by=other_user,
            access=Page.PRIVATE_ACCESS,
            is_global=True,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{private_page.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_retrieve_returns_my_own_private_page(self, session_client, workspace, create_user):
        """正向对照：自己的私有页取得到。

        与上一条是同一个可见性谓词（`_visible_page_q`）的两面。只有负向那面时，把谓词
        写成「谁都取不到私有页」也能全绿 —— 私有分区会静默变成永远空的。
        """
        own_private = Page.objects.create(
            workspace=workspace, name="我的私有", owned_by=create_user, access=Page.PRIVATE_ACCESS, is_global=True
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{own_private.id}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(own_private.id)


@pytest.mark.contract
class TestWikiPageUpdateEndpoint:
    @pytest.mark.django_db
    def test_patch_writes_description_html(self, session_client, workspace, wiki_page):
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"description_html": "<p>新正文</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert "新正文" in wiki_page.description_html

    @pytest.mark.django_db
    def test_patch_sanitizes_description_html(self, session_client, workspace, wiki_page):
        """HTML 消毒走的是 PageBinaryUpdateSerializer 那条既有路径。

        断言必须带上「留下的部分确实在」：只断言 ``"<script>" not in`` 对一个
        根本不写正文的实现同样成立（夹具页面的正文默认就是 ``<p></p>``），
        这条测试就会永远绿。
        """
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"description_html": "<p>hi</p><script>alert(1)</script>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert "<script>" not in wiki_page.description_html
        assert "<p>hi</p>" in wiki_page.description_html

    @pytest.mark.django_db
    def test_partition_key_is_rejected_as_collection_id(self, session_client, workspace, wiki_page):
        """PATCH 侧的同一件事：`collection_id: "general"`（分区键，不是 uuid）→ 400。

        与 `TestWikiPageInclude.test_partition_key_is_rejected_as_collection_id` 同款。
        两个写端点都要各有一条：前端把分区键当 uuid 送出时，两条路径都会 400，只钉一条
        挡不住另一条。正文与 collection 都必须原样 —— 见该条注释。
        """
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"collection_id": "general"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "collection_id" in response.data
        wiki_page.refresh_from_db()
        assert wiki_page.is_global is True
        assert wiki_page.collection_id is None

    @pytest.mark.django_db
    def test_patch_null_collection_moves_back_to_general(self, session_client, workspace, wiki_page):
        collection = PageCollection.objects.create(workspace=workspace, name="集合甲", owned_by=wiki_page.owned_by)
        wiki_page.collection = collection
        wiki_page.save()

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"collection_id": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert wiki_page.collection_id is None

    @pytest.mark.django_db
    def test_patch_rejects_collection_from_another_workspace(
        self, session_client, workspace, wiki_page, other_workspace, create_user
    ):
        """跨工作区挂集合必须被挡住，否则是越权写入。

        与 create 逐字同形：404 + {"error": "Collection not found."}。
        两个端点在同一个输入上给同一个答案，是 Task 4 用户裁定要保住的不对称消失。
        """
        foreign = PageCollection.objects.create(workspace=other_workspace, name="别家的集合", owned_by=create_user)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"collection_id": str(foreign.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Collection not found."
        wiki_page.refresh_from_db()
        assert wiki_page.collection_id is None

    @pytest.mark.django_db
    def test_patch_rejects_unknown_collection(self, session_client, workspace, wiki_page):
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"collection_id": "11111111-1111-1111-1111-111111111111"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Collection not found."
        wiki_page.refresh_from_db()
        assert wiki_page.collection_id is None

    @pytest.mark.django_db
    def test_patch_null_description_json_is_rejected(self, session_client, workspace, wiki_page):
        """``description_json: null`` 必须 400，且错在字段上、不落到库里。

        Page.description_json 的列是 jsonb NOT NULL（见 226 上的
        information_schema），而基类 PageBinaryUpdateSerializer 把它声明成
        allow_null=True。两边一撞，null 通过校验、直达 save()，数据库抛出
        not-null IntegrityError。

        断言必须落在「字段级错误」上，只看状态码钉不住：修复前该请求也是
        400，但走的是 BaseViewSet.handle_exception 吞掉 IntegrityError 那条
        兜底分支（views/base.py:70-84），返回的是与字段无关的
        {"error": "The payload is not valid"} —— 调用方看不出是哪个字段、
        更不知道这是一个本该在序列化层就被拒的值。修复后 null 在
        serializer.is_valid() 就被拒，错误体里带上 description_json。

        还要断言原值没被写坏：只断言 400 对一个「根本没写正文」的实现同样
        成立。
        """
        wiki_page.description_json = {"type": "doc", "keep": True}
        wiki_page.save()

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"description_json": None},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "description_json" in response.data
        wiki_page.refresh_from_db()
        assert wiki_page.description_json == {"type": "doc", "keep": True}

    @pytest.mark.django_db
    def test_patch_writes_description_json(self, session_client, workspace, wiki_page):
        """对象型 description_json 的写路径 —— 本端点此前对这条路径零覆盖。

        现有测试只用 ORM 种过 description_json，从没让它穿过 PATCH。上面那条
        500 正是因此才没被发现：这条钉住「修好之后正常对象仍然写得进去」。
        """
        payload = {"type": "doc", "content": [{"type": "paragraph"}]}

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"description_json": payload},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["description_json"] == payload
        wiki_page.refresh_from_db()
        assert wiki_page.description_json == payload

    @pytest.mark.django_db
    def test_rejected_collection_does_not_write_the_body(
        self, session_client, workspace, wiki_page, other_workspace, create_user
    ):
        """集合校验必须排在写之前：被 404 挡下的请求不得已经改过正文。

        视图里 get_object_or_404 → is_valid → 集合前置查 → serializer.save()
        这个顺序是隐式不变量，此前没有任何测试钉住它。将来谁把集合校验挪到
        save() 之后，这条请求就会「先悄悄写正文、再回 404」—— 调用方以为整
        个请求被拒，正文却已经变了。这条让那次挪动立刻变红。
        """
        foreign = PageCollection.objects.create(workspace=other_workspace, name="别家的集合", owned_by=create_user)
        wiki_page.description_html = "<p>原始</p>"
        wiki_page.save()

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"collection_id": str(foreign.id), "description_html": "<p>不该被写进去</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Collection not found."
        wiki_page.refresh_from_db()
        assert wiki_page.description_html == "<p>原始</p>"
        assert wiki_page.collection_id is None


@pytest.mark.contract
class TestWikiPageCandidates:
    @pytest.mark.django_db
    def test_candidates_are_the_uncollected_pages(self, session_client, workspace, wiki_page, project_page):
        """候选 = 本工作区 is_global=False 的页面；已收录的不在其中。"""
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        assert response.status_code == status.HTTP_200_OK
        ids = [item["id"] for item in response.data]
        assert str(project_page.id) in ids
        assert str(wiki_page.id) not in ids

    @pytest.mark.django_db
    def test_candidates_are_workspace_scoped(
        self, session_client, workspace, project_page, other_workspace, create_user
    ):
        """别的工作区的未收录页面不能出现。"""
        foreign = Page.objects.create(
            workspace=other_workspace,
            name="别家的未收录",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        ids = [item["id"] for item in response.data]
        # 先断言列表非空：否则下面那句 not in 在空列表上恒真，这条测试就是空的。
        assert str(project_page.id) in ids
        assert str(foreign.id) not in ids

    @pytest.mark.django_db
    def test_candidates_hide_other_users_private_pages(self, session_client, workspace, other_user, project_page):
        """别人的**私有**未收录页不能出现在候选里。

        与 :87（列表 private 分区）、:215（POST 收录）、:390（详情页）三条同款不变量：
        私有页面必须在 _visible_page_q 这一处滤掉，任何 action 都不能绕过去。
        """
        other_private = Page.objects.create(
            workspace=workspace,
            name="同事的私有未收录页",
            owned_by=other_user,
            access=Page.PRIVATE_ACCESS,
            is_global=False,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        ids = [item["id"] for item in response.data]
        assert str(project_page.id) in ids
        assert str(other_private.id) not in ids

    @pytest.mark.django_db
    def test_candidates_exclude_own_private_pages(self, session_client, workspace, create_user, project_page):
        """**自己**的私有未收录页也不做候选。

        私有页的归属由 `access` 决定，不由 `collection_id` 决定：`resolve_collection_key`
        的优先级是 archived > private > 用户集合 > general，所以即便带着 collection_id 收录，
        它也会落到 private 分区，而不是用户当时所在的那个分区。摆进候选只会让用户以为
        「收录到这里」，实际去了别处 —— 与 `test_candidates_hide_other_users_private_pages`
        是同一处过滤的两个方向，少了哪一条都锁不住 `.exclude(access=PRIVATE_ACCESS)`。
        """
        own_private = Page.objects.create(
            workspace=workspace,
            name="我自己的私有未收录页",
            owned_by=create_user,
            access=Page.PRIVATE_ACCESS,
            is_global=False,
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        ids = [item["id"] for item in response.data]
        assert str(project_page.id) in ids
        assert str(own_private.id) not in ids

    @pytest.mark.django_db
    def test_candidates_exclude_archived_pages(self, session_client, workspace, create_user, project_page):
        """归档页也不做候选 —— 与私有页同一类误导。

        `resolve_collection_key` 的**第一**优先级就是 archived：归档页的归属由 `archived_at`
        决定，从 general 收录它，它会落到「归档」分区而不是当前分区 —— 刷新后列表里看不到，
        可 toast 却说加了一条。与私有页是同一个问题，所以同一条处置。
        """
        archived = Page.objects.create(
            workspace=workspace,
            name="已归档的未收录页",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
            # `Page.archived_at` 是 **DateField**（`db/models/page.py:47`），给 date 不是 datetime。
            archived_at=date(2026, 1, 1),
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        ids = [item["id"] for item in response.data]
        assert str(project_page.id) in ids
        assert str(archived.id) not in ids

    @pytest.mark.django_db
    def test_candidates_only_include_pages_from_projects_i_joined(
        self, session_client, workspace, create_user, other_user
    ):
        """候选只收「我参与的项目」的页面（+ 不属于任何项目的页面）。

        上游取页面要求「是我参与的项目」（`views/page/base.py:151-155`），而候选分支此前
        只筛工作区 —— 一个不是项目 X 成员的工作区成员，能在弹窗里看到 X 的公开未收录页
        标题，并把它收录进 Wiki。两个方向都钉：同事项目里的页**不在**，我项目里的页**在**
        （少了后半句，把候选收窄成空列表也能过）。

        `other_user` 在本文件与 `conftest.py` 里**都不是** workspace 成员，所以这里
        自己建成员行，不依赖 fixture。
        """
        # 同事在 workspace 里、也在自己的项目里；create_user 两边都不是
        WorkspaceMember.objects.create(workspace=workspace, member=other_user, role=20)
        their_project = Project.objects.create(
            name="同事的项目", identifier="THEIRS", workspace=workspace, created_by=other_user
        )
        ProjectMember.objects.create(project=their_project, member=other_user, workspace=workspace, role=20)
        their_page = Page.objects.create(
            workspace=workspace,
            name="同事项目里的未收录页",
            owned_by=other_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )
        ProjectPage.objects.create(project=their_project, page=their_page, workspace=workspace)

        # 正向对照：我参与的项目
        my_project = Project.objects.create(
            name="我的项目", identifier="MINE", workspace=workspace, created_by=create_user
        )
        ProjectMember.objects.create(project=my_project, member=create_user, workspace=workspace, role=20)
        my_page = Page.objects.create(
            workspace=workspace,
            name="我项目里的未收录页",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )
        ProjectPage.objects.create(project=my_project, page=my_page, workspace=workspace)

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        assert response.status_code == status.HTTP_200_OK
        ids = [item["id"] for item in response.data]
        assert str(my_page.id) in ids
        assert str(their_page.id) not in ids

    @pytest.mark.django_db
    def test_a_page_in_two_joined_projects_appears_once(self, session_client, workspace, create_user):
        """同一页面经两个我参与的项目 join 出两行，候选里只能出现一次。

        `projects__project_projectmember__member` 是多对多 join，没有 `distinct()` 就会重复。
        候选在 UI 上是一条一条渲染的，重复**看得见**（上游 `views/page/base.py` 的同类
        join 靠前端按 id 去重掩盖了这个问题，这里没有那层掩护）。这条钉住候选分支的
        `.distinct()` —— 它和项目成员过滤是同一次修复的两半。
        """
        page = Page.objects.create(
            workspace=workspace,
            name="跨两个项目",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )
        for identifier in ("PRJA", "PRJB"):
            project = Project.objects.create(
                name=f"项目 {identifier}", identifier=identifier, workspace=workspace, created_by=create_user
            )
            ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
            ProjectPage.objects.create(project=project, page=page, workspace=workspace)

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        assert response.status_code == status.HTTP_200_OK
        ids = [item["id"] for item in response.data]
        assert ids.count(str(page.id)) == 1, f"候选里出现了 {ids.count(str(page.id))} 次：{ids!r}"

    @pytest.mark.django_db
    def test_candidates_exclude_pages_of_deactivated_project_members(
        self, session_client, workspace, create_user, project_page
    ):
        """被**移出**项目的人，不该再看到那个项目里的候选页。

        把成员移出项目**不会删 `ProjectMember` 行**，只是把 `is_active` 置 False
        （`views/project/member.py:319` 移除、`:347` 主动退出；批量路径
        `views/workspace/member.py:146,200`）。所以上游读取路径显式带了
        `projects__project_projectmember__is_active=True`（`views/page/base.py:157`）——
        少了它，「我参与的 A 项目」这个条件会被一行**已停用**的成员记录满足，被移出
        项目的人仍能在「添加页面」弹窗里看到该项目未收录页的标题，正是候选收窄要堵的
        那类泄漏。

        去掉 `is_active=True` 后这条必然失败：`member=create_user` 单条件就会匹配到
        那行 `is_active=False` 的成员记录。

        `project_page`（不属于任何项目）是正向对照，保证下面那句 `not in` 不空洞。
        """
        project = Project.objects.create(
            name="我已不在的项目", identifier="LEFT", workspace=workspace, created_by=create_user
        )
        # 被移出的人：行还在，只是 is_active=False
        ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20, is_active=False)
        stranded = Page.objects.create(
            workspace=workspace,
            name="被移出项目里的未收录页",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )
        ProjectPage.objects.create(project=project, page=stranded, workspace=workspace)

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        assert response.status_code == status.HTTP_200_OK
        ids = [item["id"] for item in response.data]
        assert str(project_page.id) in ids
        assert str(stranded.id) not in ids

    @pytest.mark.django_db
    def test_candidates_exclude_pages_from_archived_projects(
        self, session_client, workspace, create_user, project_page
    ):
        """归档项目下的页面也不做候选 —— 上游读取路径显式排除归档项目。

        归档不会把项目从 M2M join 里摘掉（`db/models/project.py` 对 `Project` 没有自定义
        manager），所以上游才显式带了 `projects__archived_at__isnull=True`
        （`views/page/base.py:158`）。少了它，一个**完全正常在册**的成员照样能在候选里
        看到已归档项目下未收录页的标题。

        `Project.archived_at` 是 **DateTimeField**（`db/models/project.py:114`），
        与 `Page.archived_at`（DateField）不同，给 datetime 不是 date。
        """
        archived_project = Project.objects.create(
            name="已归档的项目",
            identifier="ARCH",
            workspace=workspace,
            created_by=create_user,
            archived_at=timezone.now(),
        )
        # 成员关系本身完全正常 —— 这条测试钉的是**项目**归档，不是成员被停用
        ProjectMember.objects.create(project=archived_project, member=create_user, workspace=workspace, role=20)
        archived_child = Page.objects.create(
            workspace=workspace,
            name="归档项目里的未收录页",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )
        ProjectPage.objects.create(project=archived_project, page=archived_child, workspace=workspace)

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        assert response.status_code == status.HTTP_200_OK
        ids = [item["id"] for item in response.data]
        assert str(project_page.id) in ids
        assert str(archived_child.id) not in ids

    @pytest.mark.django_db
    def test_candidates_need_one_member_row_to_satisfy_every_condition(
        self, session_client, workspace, create_user, other_user, project_page
    ):
        """三个条件必须落在**同一行** join 记录上 —— 这条测试是唯一钉住「同一条 Q」的形状。

        `projects__project_projectmember__*` 是多对多 join：Django 只在**同一个**
        `Q`/`.filter()` 调用里复用 join，拆成两次 `.filter()` 会各生成一个 join，
        于是不同行可以分别满足不同条件。

        波 D 的两条同主题测试（`..._only_include_pages_from_projects_i_joined`、
        `..._exclude_pages_of_deactivated_project_members`）各自只建**一行**成员记录，
        拆分形式下每条 `.filter()` 都能在同一行上分别满足 —— 拆分与合并结果相同，
        两条测试照样全绿。**只有两行**能把两种实现分开。

        这里项目下有两行成员记录：
          - (member=create_user, is_active=False)  ← 被移出项目的人
          - (member=other_user,  is_active=True)   ← 同事仍在册
        合并形式：要放行，得有一行**同时**满足 member=create_user 与 is_active=True ——
        不存在 ⇒ 该页被排除（绿）。拆分形式：join1 命中第一行、join2 命中第二行，
        而两行**同属同一个项目** ⇒ 页面被放行（红，正是这次修复要堵的泄漏）。

        `project_page`（不属于任何项目）是正向对照，保证下面那句 `not in` 不空洞。
        """
        # create_user 与 other_user 都是工作区成员（两个 fixture 都不提供这层关系）。
        WorkspaceMember.objects.create(workspace=workspace, member=other_user, role=20)

        project = Project.objects.create(
            name="有两行成员记录的项目", identifier="TWOROW", workspace=workspace, created_by=other_user
        )
        # 被移出的我：行还在，只是 is_active=False
        ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20, is_active=False)
        # 仍在册的同事
        ProjectMember.objects.create(project=project, member=other_user, workspace=workspace, role=20)

        leak = Page.objects.create(
            workspace=workspace,
            name="两行成员项目里的未收录页",
            owned_by=other_user,
            access=Page.PUBLIC_ACCESS,
            is_global=False,
        )
        ProjectPage.objects.create(project=project, page=leak, workspace=workspace)

        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?include_candidates=true")

        assert response.status_code == status.HTTP_200_OK
        ids = [item["id"] for item in response.data]
        assert str(project_page.id) in ids
        assert str(leak.id) not in ids, (
            "能同时满足 member=create_user 与 is_active=True 的**同一行**成员记录并不存在"
            "（我那一行 is_active=False，在册那一行属于同事），该页不该出现在候选里。"
            "它出现了 ⇒ 三个条件被拆到了各自的 join 行上，多对多 join 的绑定已经失效。"
        )


@pytest.mark.contract
class TestWikiPageGuestWriteAccess:
    """写入端点对 GUEST 关闭。

    **这是用户 2026-09-27 的明确裁定，有意偏离设计 §4.2** —— 设计原文与计划原文写的是
    写操作 `WorkspaceMember` 即可（`[ADMIN, MEMBER, GUEST]`）。上游对**同一个 Page 对象**
    （`apps/api/plane/app/permissions/page.py:100-130`）是 POST/PUT/PATCH → ADMIN/MEMBER、
    DELETE → 仅 ADMIN，这里按用户裁定收窄到 ADMIN/MEMBER。
    只收窄**写**：list / retrieve 原样保留 GUEST（见本类最后一条正向对照）。
    """

    @pytest.mark.django_db
    def test_guest_cannot_include_pages(self, guest_client, workspace, project_page):
        response = guest_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/",
            {"page_ids": [str(project_page.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        project_page.refresh_from_db()
        assert project_page.is_global is False

    @pytest.mark.django_db
    def test_guest_cannot_remove_pages(self, guest_client, workspace, wiki_page):
        response = guest_client.delete(f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        wiki_page.refresh_from_db()
        assert wiki_page.is_global is True

    @pytest.mark.django_db
    def test_guest_cannot_write_page_bodies(self, guest_client, workspace, wiki_page):
        """partial_update 是三个写端点里最要紧的一个（它写正文）。"""
        response = guest_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"description_html": "<p>guest 写的</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        wiki_page.refresh_from_db()
        assert "guest 写的" not in wiki_page.description_html

    @pytest.mark.django_db
    def test_guest_can_still_read_the_wiki(self, guest_client, workspace, wiki_page):
        """正向对照：收窄的**只有**写，没有误伤读。"""
        listed = guest_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/?collection=general")
        detailed = guest_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/")

        assert listed.status_code == status.HTTP_200_OK
        assert detailed.status_code == status.HTTP_200_OK


@pytest.mark.contract
class TestWikiPageTitleUpdate:
    """The collaboration server PATCHes ``{name: ...}`` to rename a wiki page.

    ``WikiPageUpdateSerializer`` extends a plain ``serializers.Serializer``, not
    a ``ModelSerializer``: ``name`` is not derived from the model, DRF silently
    ignores unknown keys, and ``update()`` assigns fields by hand. So accepting
    a title takes two edits — the field declaration *and* the assignment. With
    only the first, a rename returns 200 and changes nothing.

    Both live title paths depend on this: ``title-sync.ts`` (on document load,
    migrating an old title into the Yjs binary) and ``title-update-manager.ts``
    (debounced, on every keystroke in the title field).
    """

    @pytest.mark.django_db
    def test_patch_updates_the_name(self, session_client, workspace, wiki_page):
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"name": "改过的标题"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert wiki_page.name == "改过的标题"
        # The response is what the live server reads back; an assertion on the
        # DB alone would pass even if the serializer dropped the field.
        assert response.data["name"] == "改过的标题"

    @pytest.mark.django_db
    def test_patch_updates_name_and_collection_together(self, session_client, workspace, wiki_page):
        """Both groups are documented as independently optional and combinable."""
        # 两个 import 都不必写：`PageCollection` 在本文件模块级已导入（:15），
        # `uuid4` 也已导入（:6）**且本测试根本不用它** ——
        # 留着未使用的局部 import 会吃一条 ruff F401，直接卡住 Python 那道门。
        collection = PageCollection.objects.create(workspace=workspace, name="目标集合", owned_by=wiki_page.owned_by)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"name": "双改", "collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert wiki_page.name == "双改"
        assert str(wiki_page.collection_id) == str(collection.id)

    @pytest.mark.django_db
    def test_patch_accepts_an_empty_name(self, session_client, workspace, wiki_page):
        """``Page.name`` is ``TextField(blank=True)`` — a blank title is legal.

        Pinned because the natural instinct is ``required=True``, which would
        turn the editor's "clear the title" gesture into a 400.
        """
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"name": ""},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert wiki_page.name == ""

    @pytest.mark.django_db
    def test_patch_with_only_a_name_does_not_touch_the_description(self, session_client, workspace, wiki_page):
        """A rename must not blank the body.

        ``PageBinaryUpdateSerializer.update()`` writes every description field
        present in ``validated_data``; if ``name`` were left in the dict it
        would be ignored there, and if the collection branch were widened
        carelessly it could save a stale instance over fresh content.
        """
        wiki_page.description_html = "<p>原有正文</p>"
        wiki_page.save(update_fields=["description_html"])

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"name": "只改标题"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert wiki_page.name == "只改标题"
        assert wiki_page.description_html == "<p>原有正文</p>"


@pytest.mark.contract
class TestRenameMovesTheMirror:
    """A rename must carry the page's vault mirror with it.

    The mirror path is name-derived (``utils/markdown_storage.py`` ``_file_stem``),
    and ``WikiPageViewSet.partial_update`` is the route the collaboration server's
    title sync PATCHes. Without the move, a rename leaves the old file behind and
    the next body write drops a second one under the new title — two files sharing
    one ``frontmatter.id``, inside the user's real Obsidian vault.

    ``isolate_markdown_mirror`` (``conftest.py:19-42``) is autouse, so nothing here
    can reach that vault.
    """

    @pytest.fixture
    def linked_page(self, workspace, create_user):
        """A wiki page that belongs to a project — the mirrorable shape."""
        project = Project.objects.create(name="镜像项目", identifier="MIR", workspace=workspace, created_by=create_user)
        ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
        page = Page.objects.create(
            workspace=workspace,
            name="镜像前的标题",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
        )
        ProjectPage.objects.create(project=project, page=page, workspace=workspace)
        return page

    @pytest.mark.django_db
    def test_rename_moves_the_mirrored_file(self, session_client, workspace, linked_page, isolate_markdown_mirror):
        # 先用正文端点造出镜像 —— 真实顺序就是「先写正文，再改标题」。
        written = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{linked_page.id}/description/",
            {"description_html": "<p>镜像我</p>"},
            format="json",
        )
        assert written.status_code == status.HTTP_200_OK

        before = list(isolate_markdown_mirror.rglob("*.md"))
        assert len(before) == 1, f"正文端点应当写出恰好一份镜像：{before!r}"
        old_file = before[0]
        old_text = old_file.read_text(encoding="utf-8")
        assert "镜像我" in old_text

        renamed = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{linked_page.id}/",
            {"name": "改名后的标题"},
            format="json",
        )

        assert renamed.status_code == status.HTTP_200_OK
        linked_page.refresh_from_db()
        assert linked_page.name == "改名后的标题"

        after = list(isolate_markdown_mirror.rglob("*.md"))
        # 关键的一行：不断它的话，「搬了」和「没搬」都只有一份文件 —— 测试会空转。
        assert not old_file.exists(), "改名前的镜像文件必须被搬走，不能留在原路径"
        assert len(after) == 1, f"镜像应当被搬移而不是复制出第二份：{after!r}"
        # 同一份内容换了路径，而不是在别处新写了一个空文件。
        assert after[0].read_text(encoding="utf-8") == old_text

    @pytest.mark.django_db
    def test_rename_without_a_project_writes_nothing(
        self, session_client, workspace, wiki_page, isolate_markdown_mirror
    ):
        """No project link -> no directory -> skip and log, the same ruling as the write.

        Guards the failure direction (design §2.3c): an implementation that guessed
        a directory would put a file somewhere the user did not ask for. The rename
        itself must still succeed — a page whose markdown cannot be mirrored is
        still a page the user renamed.
        """
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/",
            {"name": "无项目也改名"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        wiki_page.refresh_from_db()
        assert wiki_page.name == "无项目也改名"
        assert list(isolate_markdown_mirror.rglob("*.md")) == []
