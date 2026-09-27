# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

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


@pytest.fixture
def outsider(db):
    """一个**不是** ``workspace`` 成员的用户。

    刻意不建 WorkspaceMember 行 —— 要测的就是 `@allow_permission` 把非成员挡在外面。
    `User.username` 是 unique=True，必须给唯一值（create_user fixture 已占用 ""）。
    """
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"outsider-{unique_id}@plane.so",
        username=f"outsider_{unique_id}",
        first_name="Outsider",
        last_name="User",
    )
    user.set_password("test-password")
    user.save()
    return user


@pytest.fixture
def outsider_client(outsider):
    """以**非工作区成员**身份认证的客户端。

    **不能复用 session_client** —— 它已经 force_authenticate 成 create_user（工作区成员）了，
    再认证一次只会把「非成员被拒」测成「成员被拒」，403 照样绿。
    """
    client = APIClient()
    client.force_authenticate(user=outsider)
    return client


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

    @pytest.mark.django_db
    def test_response_shape_is_pinned(self, session_client, workspace, wiki_page):
        """钉住响应对象的形状，而不只是 ``key`` 的顺序。

        上面那几条都只看 ``predefined`` 的 ``key`` 与 ``page_count``：给
        ``PageCollectionSerializer`` 加一个字段、或把 ``id``/``name`` 改名，
        整个套件照样全绿，而前端的 ``collections[].id`` 会静默变成 undefined。
        这里把两个分区各自的字段和用户集合行的字段都钉死（字段名以实际的
        ``PageCollectionSerializer.Meta.fields`` 为准：id / name / sort_order / created_at）。
        """
        collection = PageCollection.objects.create(workspace=workspace, name="设计文档", owned_by=wiki_page.owned_by)

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data.keys()) == {"predefined", "collections"}

        for item in response.data["predefined"]:
            assert set(item.keys()) == {"key", "page_count"}

        assert len(response.data["collections"]) == 1
        row = response.data["collections"][0]
        assert set(row.keys()) == {"id", "name", "sort_order", "created_at", "page_count"}
        # `id` 是 `BaseSerializer.id`（PrimaryKeyRelatedField）原样吐出的 UUID 对象，
        # 不是 str —— 对 UUID 走 `to_representation` 只把模型实例换成 `.pk`，
        # 已经是 pk 的值原样返回。线上由 JSONRenderer 转成字符串，所以这里两边都
        # 取 `str()`：钉的是「字段在、值对」，不是上游那个中间表示。
        assert str(row["id"]) == str(collection.id)
        assert row["name"] == "设计文档"

    @pytest.mark.django_db
    def test_non_member_is_forbidden(self, outsider_client, workspace, wiki_page):
        """非工作区成员 → 403。

        把 ``PageCollectionViewSet.list`` 上的 ``@allow_permission`` 整个删掉，
        ``page-collections/`` 就对**任何已认证用户**开放（连 ``get_queryset`` 都只按
        路径里的 slug 过滤），而在此之前没有任何测试会失败 —— 这条是那个缺口的钉子。
        断言 403 而不是 404：装饰器拒绝时不区分资源是否存在。
        """
        response = outsider_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
class TestWikiPageCollectionMoveAuditStamp:
    @pytest.mark.django_db
    def test_moving_between_collections_refreshes_updated_at_only(
        self, session_client, workspace, create_user, other_user
    ):
        """换集合必须刷新 updated_at，且**不得**把操作者盖到 updated_by 上。

        `Page.updated_at` 是 auto_now（`db/mixins.py`），而 `_save_table` 只对
        `update_fields` 里列出的字段调 `pre_save` ⇒ `save(update_fields=["collection"])`
        是「时间戳和审计位两者都不写」。收录/移出是**刻意**刷新的（见 `collection.py`
        的 create/destroy，同款断言在
        `test_wiki_pages_app.py::test_including_and_removing_refresh_updated_at`），
        而且 `-updated_at` 是候选列表的排序键 —— 换集合是第三条写入路径，此前没人看见。

        另一半是 `updated_by`：与收录/移出同一条裁定 —— 换集合不是一次内容编辑，
        审计位必须保持不动。基线刻意取**非空且不是 create_user** 的 `other_user`：
        非空基线还排除了「顺带写成 None」这种改法。

        先按到 30 天前（同一条绕过 auto_now 的 `update()`），断言 `>` 才有确定基准，
        不依赖两次调用之间的挂钟差。
        """
        collection = PageCollection.objects.create(workspace=workspace, name="设计文档", owned_by=create_user)
        page = Page.objects.create(
            workspace=workspace,
            name="页",
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            is_global=True,
        )
        stale = timezone.now() - timedelta(days=30)
        Page.objects.filter(id=page.id).update(updated_at=stale, updated_by=other_user)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.collection_id == collection.id
        assert page.updated_at > stale
        assert page.updated_by_id == other_user.id
