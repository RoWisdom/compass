# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""把页面/文件夹**移到指定位置**（罗盘 Round E，设计 §4.1–4.2、§4.7）。

目标是「位置」，不是「集合」：集合顶层与树里任意一行（文件夹**或**页面）都算。

本文件只覆盖 **DB 侧**（`parent` / `collection_id` 怎么变）。镜像搬移在
`test_wiki_reparent_mirror_app.py` 里单独覆盖 —— 两件事的失败方向不同，
混在一个文件里会让「DB 对了但文件没搬」看起来像通过。
"""

from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, Workspace


def _wiki_page(workspace, user, name, **kwargs):
    """A public page included in the wiki — the shape the metadata route accepts."""
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
    """一行文件夹。与 `_wiki_page` 的唯一差别就是 `node_type`。"""
    kwargs.setdefault("node_type", Page.NODE_TYPE_FOLDER)
    return _wiki_page(workspace, user, name, **kwargs)


@pytest.fixture
def collection(workspace, create_user):
    return PageCollection.objects.create(workspace=workspace, name="目标集合", owned_by=create_user)


@pytest.fixture
def tree(workspace, create_user):
    """一条最小但够用的树：

        A（文件夹）
        └── B（文件夹）
            └── t1（页面）
        outside（页面，与 A 无关）
    """
    a = _folder(workspace, create_user, "A")
    b = _folder(workspace, create_user, "B", parent=a)
    t1 = _wiki_page(workspace, create_user, "t1", parent=b)
    outside = _wiki_page(workspace, create_user, "outside")
    return {"a": a, "b": b, "t1": t1, "outside": outside}


def _url(workspace, page):
    return f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/"


@pytest.mark.contract
class TestMovingIntoAPosition:
    @pytest.mark.django_db
    def test_a_page_can_be_moved_under_a_folder(self, session_client, workspace, tree):
        response = session_client.patch(_url(workspace, tree["outside"]), {"parent": str(tree["a"].id)}, format="json")

        assert response.status_code == status.HTTP_200_OK
        tree["outside"].refresh_from_db()
        assert tree["outside"].parent_id == tree["a"].id

    @pytest.mark.django_db
    def test_a_page_can_be_moved_under_another_page(self, session_client, workspace, tree):
        """页面也是合法容器（E-8）：本仓从 Round C 起支持子页面。"""
        response = session_client.patch(_url(workspace, tree["outside"]), {"parent": str(tree["t1"].id)}, format="json")

        assert response.status_code == status.HTTP_200_OK
        tree["outside"].refresh_from_db()
        assert tree["outside"].parent_id == tree["t1"].id

    @pytest.mark.django_db
    def test_the_targets_collection_wins_over_the_one_in_the_same_request(
        self, session_client, workspace, tree, collection
    ):
        """位置决定集合。同一请求里既给 parent 又给 collection_id 时，以 parent 为准。"""
        tree["a"].collection = collection
        tree["a"].save()

        response = session_client.patch(
            _url(workspace, tree["outside"]),
            {"parent": str(tree["a"].id), "collection_id": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        tree["outside"].refresh_from_db()
        assert tree["outside"].collection_id == collection.id

    @pytest.mark.django_db
    def test_moving_into_a_page_with_no_collection_lands_in_general(
        self, session_client, workspace, tree, collection
    ):
        """目标是「常规」里的页面 ⇒ 目标集合推导为 `null`，页面回到 general。"""
        tree["outside"].collection = collection
        tree["outside"].save()

        response = session_client.patch(_url(workspace, tree["outside"]), {"parent": str(tree["a"].id)}, format="json")

        assert response.status_code == status.HTTP_200_OK
        tree["outside"].refresh_from_db()
        assert tree["outside"].collection_id is None

    @pytest.mark.django_db
    def test_a_page_can_be_moved_out_to_the_collection_root(self, session_client, workspace, tree):
        """显式传 null = 移到集合顶层。"""
        response = session_client.patch(_url(workspace, tree["t1"]), {"parent": None}, format="json")

        assert response.status_code == status.HTTP_200_OK
        tree["t1"].refresh_from_db()
        assert tree["t1"].parent_id is None

    @pytest.mark.django_db
    def test_a_folder_can_be_moved_under_another_folder(self, session_client, workspace, tree, create_user):
        """文件夹也是合法容器：B 从 A 挪到一个与它无关的宿主文件夹下。"""
        host = _folder(workspace, create_user, "宿主")

        response = session_client.patch(_url(workspace, tree["b"]), {"parent": str(host.id)}, format="json")

        assert response.status_code == status.HTTP_200_OK
        tree["b"].refresh_from_db()
        assert tree["b"].parent_id == host.id

    @pytest.mark.django_db
    def test_omitting_parent_leaves_it_alone(self, session_client, workspace, tree):
        """PATCH 语义：没传这个键就不动 —— 协同编辑器的标题同步正是这么发的。"""
        response = session_client.patch(_url(workspace, tree["t1"]), {"name": "t1-改"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        tree["t1"].refresh_from_db()
        assert tree["t1"].parent_id == tree["b"].id
        assert tree["t1"].name == "t1-改"


@pytest.mark.contract
class TestMoveRejectsTheImpossible:
    @pytest.mark.django_db
    def test_moving_a_page_into_itself_is_400(self, session_client, workspace, tree):
        response = session_client.patch(_url(workspace, tree["a"]), {"parent": str(tree["a"].id)}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "Cannot move a page into itself or its own descendant."
        tree["a"].refresh_from_db()
        assert tree["a"].parent_id is None

    @pytest.mark.django_db
    def test_moving_a_folder_into_its_own_descendant_is_400(self, session_client, workspace, tree):
        """A → B 会让 A 变成 B 的子节点，而 B 已经是 A 的后代 —— 环。"""
        response = session_client.patch(_url(workspace, tree["a"]), {"parent": str(tree["b"].id)}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "Cannot move a page into itself or its own descendant."
        tree["a"].refresh_from_db()
        assert tree["a"].parent_id is None

    @pytest.mark.django_db
    def test_an_unknown_parent_is_404_and_nothing_changes(self, session_client, workspace, tree):
        response = session_client.patch(_url(workspace, tree["outside"]), {"parent": str(uuid4())}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Parent page not found."
        tree["outside"].refresh_from_db()
        assert tree["outside"].parent_id is None

    @pytest.mark.django_db
    def test_a_page_that_is_not_in_the_wiki_is_not_a_valid_parent(self, session_client, workspace, tree):
        """父必须**已收录** —— 否则子页会挂在一个永远看不见的孤儿下面。"""
        tree["outside"].is_global = False
        tree["outside"].save()

        response = session_client.patch(_url(workspace, tree["t1"]), {"parent": str(tree["outside"].id)}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Parent page not found."
        tree["t1"].refresh_from_db()
        assert tree["t1"].parent_id == tree["b"].id

    @pytest.mark.django_db
    def test_a_folder_in_another_workspace_is_not_a_valid_parent(
        self, session_client, workspace, tree, create_user
    ):
        """形状与「不存在」一致（404），不是 403 —— 不泄漏存在性。"""
        other = Workspace.objects.create(name="别的", slug="other-move-ws", owner=create_user)
        foreign = _folder(other, create_user, "别家的文件夹")

        response = session_client.patch(_url(workspace, tree["outside"]), {"parent": str(foreign.id)}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Parent page not found."
        foreign.refresh_from_db()
        assert foreign.parent_id is None


@pytest.mark.contract
class TestMoveCascadesTheSubtree:
    @pytest.mark.django_db
    def test_descendants_follow_into_the_new_collection(self, session_client, workspace, tree, collection, create_user):
        """把 A 移到目标集合里 ⇒ A 的整棵子树换集合（E-10）。"""
        host = _folder(workspace, create_user, "宿主", collection=collection)

        response = session_client.patch(_url(workspace, tree["a"]), {"parent": str(host.id)}, format="json")

        assert response.status_code == status.HTTP_200_OK
        for key in ("a", "b", "t1"):
            tree[key].refresh_from_db()
            assert tree[key].collection_id == collection.id


@pytest.mark.contract
class TestTheBodyRouteWillNotMoveAnything:
    """设计 §4.7：正文端点复用了同一个序列化器，必须显式拒绝 `parent`。"""

    @pytest.mark.django_db
    def test_the_description_route_rejects_parent(self, session_client, workspace, tree):
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{tree['outside'].id}/description/",
            {"parent": str(tree["a"].id), "description_html": "<p>x</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "Use the wiki-pages endpoint to move a page."
        tree["outside"].refresh_from_db()
        assert tree["outside"].parent_id is None

    @pytest.mark.django_db
    def test_the_description_route_still_accepts_name_and_collection(
        self, session_client, workspace, tree, collection
    ):
        """守住「不误伤」：这条路由对改名 / 换集合的既有容忍必须原样保留。"""
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{tree['outside'].id}/description/",
            {"name": "改名了", "collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        tree["outside"].refresh_from_db()
        assert tree["outside"].name == "改名了"
        assert tree["outside"].collection_id == collection.id
