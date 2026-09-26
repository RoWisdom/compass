# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, User, Workspace, WorkspaceMember


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
        response = session_client.get(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{other_workspace_wiki_page.id}/"
        )

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
