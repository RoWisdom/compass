# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""删除集合（罗盘 Round H，设计 §2/§4）。语义照 Confluence Cloud：

  · 集合这个**容器**没了，里面的页面与文件夹**一个都不删**；
  · 它们整体上浮到「常规」（预置分区，`collection_id` 置空即自动落进去）；
  · 结构原封不动 —— `parent` 一个都不动；
  · vault 里的镜像搬进 `3-Wiki/常规/`，空目录删掉，非空原样留下。

本文件分三段：守卫（本任务的「常规」重名）、端点与行语义（Task 4）、镜像（Task 5）。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, User, Workspace, WorkspaceMember

GENERAL = "常规"


def _collection(workspace, user, name, **kwargs):
    return PageCollection.objects.create(workspace=workspace, name=name, owned_by=user, **kwargs)


def _url(workspace, collection):
    return f"/api/workspaces/{workspace.slug}/page-collections/{collection.id}/"


@pytest.fixture
def no_celery(monkeypatch):
    """把软删行时那次 Celery 级联钉成空操作。

    `SoftDeleteModel.delete()` 会 `.delay()` 一个任务（`db/mixins.py:78`），而测试设置
    （`plane/settings/test.py`）**没有** `CELERY_TASK_ALWAYS_EAGER` —— 真跑就会把一条
    消息发到 226 的 RabbitMQ 上，被**生产** worker 消费。本轮的设计本来就**不依赖**
    那条级联（页面由 ② 同步浮升，见 `destroy` 的 docstring），所以测试里钉掉它既准确
    又不污染生产队列。
    """
    monkeypatch.setattr("plane.db.mixins.soft_delete_related_objects.delay", lambda *args, **kwargs: None)


@pytest.mark.contract
class TestTheGeneralNameIsReserved:
    @pytest.mark.django_db
    def test_create_rejects_the_general_name(self, session_client, workspace):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/page-collections/", {"name": GENERAL}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data, "错误体要与序列化器校验错误同形（前端已有兜底 toast）"
        assert not PageCollection.objects.filter(workspace=workspace, name=GENERAL).exists()

    @pytest.mark.django_db
    def test_rename_into_the_general_name_is_rejected(self, session_client, workspace, create_user):
        """改名撞车也要挡 —— 不挡的话那个集合的目录会与常规的目录**是同一个文件夹**。"""
        collection = _collection(workspace, create_user, "别名叫法")

        response = session_client.patch(_url(workspace, collection), {"name": GENERAL}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data
        collection.refresh_from_db()
        assert collection.name == "别名叫法", "被拒的改名不得落库"

    @pytest.mark.django_db
    def test_a_name_that_merely_contains_it_is_fine(self, session_client, workspace, create_user):
        """判据是**相等**不是包含 —— 「常规 2」是合法集合名。"""
        response = session_client.patch(
            _url(workspace, _collection(workspace, create_user, "X")), {"name": f"{GENERAL} 2"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK


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
def collection_tree(workspace, create_user):
    """一个集合 + 里面的一页面、一文件夹、文件夹下的子页，外加一个局外页面：

        集合 A
        ├── page（页面）
        └── F（文件夹）
            └── child（页面）
        集合 B
        └── other（页面）
        outside（页面，无集合）
    """
    a = _collection(workspace, create_user, "A")
    b = _collection(workspace, create_user, "B")
    page = _wiki_page(workspace, create_user, "page", collection=a)
    folder = _folder(workspace, create_user, "F", collection=a)
    child = _wiki_page(workspace, create_user, "child", parent=folder, collection=a)
    other = _wiki_page(workspace, create_user, "other", collection=b)
    outside = _wiki_page(workspace, create_user, "outside")
    return {"a": a, "b": b, "page": page, "folder": folder, "child": child, "other": other, "outside": outside}


@pytest.mark.contract
class TestDeletingACollectionKeepsEverythingInsideIt:
    @pytest.mark.django_db
    def test_an_empty_collection_can_be_deleted(self, session_client, workspace, create_user, no_celery):
        collection = _collection(workspace, create_user, "空集合")

        response = session_client.delete(_url(workspace, collection))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not PageCollection.objects.filter(id=collection.id).exists(), "软删行不再从默认 manager 里露出来"
        collection.refresh_from_db()
        assert collection.deleted_at is not None

    @pytest.mark.django_db
    def test_deleting_the_same_collection_twice_is_a_404(self, session_client, workspace, create_user, no_celery):
        """第二次删同一个集合 ⇒ 404（软删行已被默认 manager 过滤掉）。"""
        collection = _collection(workspace, create_user, "空集合")
        assert session_client.delete(_url(workspace, collection)).status_code == status.HTTP_204_NO_CONTENT

        assert session_client.delete(_url(workspace, collection)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_pages_and_folders_stay_and_float_into_general(
        self, session_client, workspace, collection_tree, no_celery
    ):
        """核心承诺：**一行都不删**，全部落到常规，结构原封不动。"""
        tree = collection_tree
        folders = {tree["folder"].id: tree["folder"].parent_id}
        rows = {key: (tree[key], tree[key].parent_id) for key in ("page", "folder", "child")}

        response = session_client.delete(_url(workspace, tree["a"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for key, (row, parent_id) in rows.items():
            row.refresh_from_db()
            assert row.deleted_at is None, f"{key} 不得被删"
            assert row.is_global is True, f"{key} 不得出 Wiki"
            assert row.collection_id is None, f"{key} 应上浮到常规（collection_id 置空）"
            assert row.parent_id == parent_id, f"{key} 的父不得动（结构原封不动）"
        assert folders[tree["folder"].id] is None, "前置：文件夹本来就是顶层"

    @pytest.mark.django_db
    def test_the_other_collection_and_pages_outside_are_untouched(
        self, session_client, workspace, collection_tree, no_celery
    ):
        session_client.delete(_url(workspace, collection_tree["a"]))

        collection_tree["b"].refresh_from_db()
        assert collection_tree["b"].deleted_at is None, "B 不该被删"
        collection_tree["other"].refresh_from_db()
        assert collection_tree["other"].collection_id == collection_tree["b"].id, "B 里的页面不动"
        collection_tree["outside"].refresh_from_db()
        assert collection_tree["outside"].collection_id is None, "本来就在常规的页面不受影响"
        assert collection_tree["outside"].deleted_at is None

    @pytest.mark.django_db
    def test_the_general_partition_count_picks_the_pages_up(
        self, session_client, workspace, create_user, no_celery
    ):
        """口径落到 `resolve_collection_key`：`collection_id` 一置空，页面就落 general。

        刻意用一棵**不带文件夹**的小树，并把断言写成**增量**：`collection_tree` 里有个
        本来就在常规的 `outside`，用绝对值会把基准挪掉；而绝对计数还会偷偷把
        「`page_count` 到底算不算文件夹节点」这条与本轮无关的既有口径钉进本测试。
        """
        collection = _collection(workspace, create_user, "A")
        _wiki_page(workspace, create_user, "p1", collection=collection)
        _wiki_page(workspace, create_user, "p2", collection=collection)

        def general_count():
            response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")
            assert response.status_code == status.HTTP_200_OK
            return next(item for item in response.data["predefined"] if item["key"] == "general")["page_count"]

        before = general_count()
        session_client.delete(_url(workspace, collection))
        assert general_count() == before + 2, "两个页面都进了常规"

    @pytest.mark.django_db
    def test_the_deleted_collection_leaves_the_list(self, session_client, workspace, collection_tree, no_celery):
        session_client.delete(_url(workspace, collection_tree["a"]))

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        ids = {str(row["id"]) for row in response.data["collections"]}
        assert str(collection_tree["a"].id) not in ids
        assert str(collection_tree["b"].id) in ids

    @pytest.mark.django_db
    def test_the_write_refreshes_updated_at_and_does_not_stamp_updated_by(
        self, session_client, workspace, collection_tree, no_celery
    ):
        """与收录/移出/换集合同一条纪律：刷新时间戳，但不把执行者盖到「最后编辑人」上。"""
        from datetime import timedelta

        from django.utils import timezone

        stale = timezone.now() - timedelta(days=30)
        Page.objects.filter(id=collection_tree["page"].id).update(updated_at=stale, updated_by=None)

        session_client.delete(_url(workspace, collection_tree["a"]))

        collection_tree["page"].refresh_from_db()
        assert collection_tree["page"].updated_at > stale
        assert collection_tree["page"].updated_by_id is None


@pytest.mark.contract
class TestTheEndpointRefusesTheUnimplementedContract:
    @pytest.mark.django_db
    def test_transfer_to_is_rejected(self, session_client, workspace, collection_tree, no_celery):
        response = session_client.delete(
            f"{_url(workspace, collection_tree['a'])}?transfer_to={collection_tree['b'].id}"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        collection_tree["a"].refresh_from_db()
        assert collection_tree["a"].deleted_at is None, "被拒的请求不得删掉任何东西"

    @pytest.mark.django_db
    def test_delete_pages_is_rejected(self, session_client, workspace, collection_tree, no_celery):
        """`?delete_pages=false` 也 400 —— 判据是**出现**，因为出现就说明调用方
        以为那份文档化的契约存在（设计 §4.1）。"""
        response = session_client.delete(f"{_url(workspace, collection_tree['a'])}?delete_pages=false")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        collection_tree["a"].refresh_from_db()
        assert collection_tree["a"].deleted_at is None


@pytest.mark.contract
class TestPermissions:
    @pytest.mark.django_db
    def test_a_collection_in_another_workspace_is_404(self, session_client, workspace, create_user, no_celery):
        other = Workspace.objects.create(name="别的", slug="other-del-ws", owner=create_user)
        foreign = _collection(other, create_user, "外人")

        response = session_client.delete(f"/api/workspaces/{workspace.slug}/page-collections/{foreign.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        foreign.refresh_from_db()
        assert foreign.deleted_at is None

    @pytest.mark.django_db
    def test_a_guest_is_forbidden(self, workspace, create_user, no_celery):
        """写端点不含 GUEST（与 `create` / `partial_update` 的权限列表逐字一致）。"""
        from rest_framework.test import APIClient

        from plane.app.permissions import ROLE

        guest = User.objects.create(email="guest-del@plane.so", username="guest-del")
        guest.set_password("test-password")
        guest.save()
        WorkspaceMember.objects.create(workspace=workspace, member=guest, role=ROLE.GUEST.value)
        client = APIClient()
        client.force_authenticate(user=guest)
        collection = _collection(workspace, create_user, "谁都能看的")

        response = client.delete(_url(workspace, collection))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        collection.refresh_from_db()
        assert collection.deleted_at is None

    @pytest.mark.django_db
    def test_a_non_member_is_forbidden(self, workspace, no_celery):
        """非工作区成员 → 403（装饰器不区分资源是否存在）。"""
        from rest_framework.test import APIClient

        outsider = User.objects.create(email="outsider-del@plane.so", username="outsider-del")
        outsider.set_password("test-password")
        outsider.save()
        client = APIClient()
        client.force_authenticate(user=outsider)
        collection = _collection(workspace, outsider, "非成员建的")

        response = client.delete(_url(workspace, collection))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        collection.refresh_from_db()
        assert collection.deleted_at is None
