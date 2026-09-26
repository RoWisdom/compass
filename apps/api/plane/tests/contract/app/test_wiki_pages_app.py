# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection


@pytest.fixture
def project_page(workspace, create_user):
    """一个普通的、未收录的项目页面。"""
    return Page.objects.create(
        workspace=workspace, name="项目里的页面", owned_by=create_user, access=Page.PUBLIC_ACCESS
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
