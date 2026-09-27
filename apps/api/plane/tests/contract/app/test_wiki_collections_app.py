# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import PageCollection, User, Workspace, WorkspaceMember


@pytest.fixture
def other_workspace(db):
    """另一个工作区（连同它的成员关系），用来验证跨工作区隔离。

    形状与 `test_wiki_pages_app.py:44-49` 同 —— `plane/tests/conftest.py` 里没有
    工作区级的 fixture，本仓先例就是各文件自己定义（见那个文件 `guest` 的注释）。
    slug 必须与那个文件用的 `other-workspace` 不同：两个文件在同一个测试库里跑。
    """
    owner = User.objects.create(email="other-collection-owner@plane.so", username="other-collection-owner")
    workspace = Workspace.objects.create(name="Other", owner=owner, slug="other-collection-workspace")
    WorkspaceMember.objects.create(workspace=workspace, member=owner, role=20)
    return workspace


@pytest.fixture
def other_workspace_collection(other_workspace):
    """别的工作区里的一个集合 —— PATCH 它必须 404。"""
    return PageCollection.objects.create(workspace=other_workspace, name="别家的集合", owned_by=other_workspace.owner)


@pytest.fixture
def guest(db, workspace):
    """本工作区里的只读成员（GUEST, role=5）。

    `plane/tests/conftest.py` 里没有 guest fixture；形状照 `test_wiki_pages_app.py:76-94`。
    `User.username` 是 unique=True 且 create_user fixture 已占用 ""，所以必须给唯一值。
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

    **不能**复用 `session_client` —— 它已经 force_authenticate 成 create_user 了，
    再认证一次只会换掉身份、把「GUEST 被拒」测成「create_user 被拒」。
    """
    client = APIClient()
    client.force_authenticate(user=guest)
    return client


@pytest.mark.contract
class TestWikiCollectionCreate:
    @pytest.mark.django_db
    def test_creates_collection(self, session_client, workspace, create_user):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": "测试目录1"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "测试目录1"
        # 新集合必然是空的；`list` 的每一行都带 page_count，两个端点同形之后
        # 前端拿到 201 的响应就能直接塞进侧栏，不必为「新建」写一个缺省的兼容分支。
        assert response.data["page_count"] == 0

        collection = PageCollection.objects.get(id=response.data["id"])
        assert collection.workspace_id == workspace.id
        assert collection.owned_by_id == create_user.id

    @pytest.mark.django_db
    def test_new_collection_appears_in_list(self, session_client, workspace):
        created = session_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": "新建的"}, format="json"
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        assert response.status_code == status.HTTP_200_OK
        assert [item["name"] for item in response.data["collections"]] == ["新建的"]
        assert response.data["collections"][0]["id"] == created.data["id"]
        assert response.data["collections"][0]["page_count"] == 0

    @pytest.mark.django_db
    @pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "}])
    def test_rejects_missing_or_blank_name(self, session_client, workspace, payload):
        """`{}` 这一条是 `allow_blank=False` 存在的**唯一**理由。

        只写 `validate_name` 挡不住缺键的请求 —— DRF 只在字段出现在载荷里时才跑
        `validate_<field>`，一个 `{}` 会绕过它建出 `name=""` 的集合，而前端
        `form.name_required` 要求非空。
        """
        response = session_client.post(f"/api/workspaces/{workspace.slug}/page-collections/", payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert PageCollection.objects.count() == 0

    @pytest.mark.django_db
    def test_accepts_255_characters(self, session_client, workspace):
        """255 是上限本身，必须通过 —— 这条是「255 而非 254」那个裁定的牙。"""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": "字" * 255}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.django_db
    def test_rejects_256_characters(self, session_client, workspace):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": "字" * 256}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert PageCollection.objects.count() == 0

    @pytest.mark.django_db
    def test_allows_duplicate_names(self, session_client, workspace):
        """模型没有唯一约束，官方亦然 —— 重名是**有意**允许的，别加去重提示。"""
        first = session_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": "同名"}, format="json"
        )
        second = session_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": "同名"}, format="json"
        )

        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_201_CREATED
        assert first.data["id"] != second.data["id"]

    @pytest.mark.django_db
    def test_guest_is_forbidden(self, guest_client, workspace):
        response = guest_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": "guest 建的"}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert PageCollection.objects.count() == 0
