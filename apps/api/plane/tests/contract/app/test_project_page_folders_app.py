# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""项目页文件夹（罗盘 Round J）—— 读、建、移三条路径。

设计：`罗盘-Projects页面文件夹-设计.md`（不进 git）。
裁定 乙：列表加一个 **opt-in** 的 `?scope=all`，**不摘**上游那条
`parent IS NULL` 过滤；不带参数时的响应必须**逐字不变**。

顺带修掉两个既有缺口 —— 它们今天看不见，是因为没有入口能建出子页：

  · `GET .../pages/<子页 id>/` 今天 **404**（`retrieve` 走的 `get_queryset()` 带着
    `parent IS NULL`，子页被吃掉）。本轮起建得出子页，它立刻变成「建得出来、打不开」。
  · `POST .../pages/ {parent: X}` 今天**也是 404**（`create` 结尾
    `self.get_queryset().get(pk=<新页 id>)` 同样被吃掉 ⇒ 抛 `Page.DoesNotExist`，
    被 `BaseViewSet.handle_exception` 的 `ObjectDoesNotExist` 分支兜成 404）。
    页面与镜像**其实都已经落库**，客户端看到的却是一个错误。
    **注意不是 500** —— `Page.DoesNotExist` 逃不出 DRF 的异常处理
    （`app/views/base.py:92-96`），实测那条 POST 返回的是
    `{'error': 'The required object does not exist.'}` + 404。

URL 前缀是 `/api/`（app 契约测试的约定，不是 `/api/v1/`）。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage


def _project(workspace, create_user):
    """本工作区里一个「请求者是活跃 ADMIN」的项目。"""
    project = Project.objects.create(
        name="罗盘项目",
        identifier="LC",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


def _page(workspace, project, user, name, **kwargs):
    """建一页并挂到项目下（`ProjectPage` 那条链接是权限与列表过滤的前提）。"""
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
    return _project(workspace, create_user)


@pytest.fixture
def tree(workspace, project, create_user):
    """A（文件夹）├── B（页面）├── C（文件夹）└── D（页面）；外加一个无关的 root。"""
    a = _page(workspace, project, create_user, "A", node_type=Page.NODE_TYPE_FOLDER)
    b = _page(workspace, project, create_user, "B", parent=a)
    c = _page(workspace, project, create_user, "C", node_type=Page.NODE_TYPE_FOLDER, parent=b)
    d = _page(workspace, project, create_user, "D", parent=c)
    root = _page(workspace, project, create_user, "root")
    return {"a": a, "b": b, "c": c, "d": d, "root": root}


def _list_url(workspace, project):
    return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/"


def _detail_url(workspace, project, page):
    return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/"


class _NoopTask:
    """把 ``page_transaction`` 换成一个不连 broker 的替身。"""

    def delay(self, **kwargs):
        return None


@pytest.fixture
def no_broker(monkeypatch):
    """项目侧 ``PageViewSet.create`` 的成功路径会 ``page_transaction.delay(...)``。

    测试环境**没有 broker**（``CELERY_BROKER_URL`` 由 ``plane/settings/common.py:319-330``
    从 ``RABBITMQ_HOST`` 拼出来，sourcing ``.env`` 后指着 226 的 RabbitMQ）。
    真去连一个消息队列不是被测行为，是本文件的噪声源 —— 换掉。

    wiki 侧的建页端点不带这条副作用，所以 ``test_wiki_page_create_app.py`` 里没有这个夹具。
    """
    from plane.app.views.page import base as page_base

    monkeypatch.setattr(page_base, "page_transaction", _NoopTask())


@pytest.mark.contract
class TestScopeAllReturnsTheWholeTree:
    @pytest.mark.django_db
    def test_returns_pages_and_folders_at_every_depth(self, session_client, workspace, project, tree):
        response = session_client.get(_list_url(workspace, project) + "?scope=all")

        assert response.status_code == status.HTTP_200_OK
        ids = {str(row["id"]) for row in response.data}
        for key in ("a", "b", "c", "d", "root"):
            assert str(tree[key].id) in ids, f"{key} 应在整棵树里"

    @pytest.mark.django_db
    def test_every_row_carries_node_type_and_parent(self, session_client, workspace, project, tree):
        response = session_client.get(_list_url(workspace, project) + "?scope=all")
        by_id = {str(row["id"]): row for row in response.data}

        assert by_id[str(tree["a"].id)]["node_type"] == Page.NODE_TYPE_FOLDER
        assert by_id[str(tree["a"].id)]["parent"] is None
        assert by_id[str(tree["b"].id)]["node_type"] == Page.NODE_TYPE_DOC
        assert str(by_id[str(tree["b"].id)]["parent"]) == str(tree["a"].id)
        assert str(by_id[str(tree["d"].id)]["parent"]) == str(tree["c"].id)


@pytest.mark.contract
class TestTheDefaultListIsUnchanged:
    """裁定 乙的锁：不带 `scope` 的那条路径**逐字不变**。"""

    @pytest.mark.django_db
    def test_without_scope_only_roots_come_back(self, session_client, workspace, project, tree):
        response = session_client.get(_list_url(workspace, project))

        assert response.status_code == status.HTTP_200_OK
        ids = {str(row["id"]) for row in response.data}
        assert ids == {str(tree["a"].id), str(tree["root"].id)}

    @pytest.mark.django_db
    def test_without_scope_there_is_no_node_type_key(self, session_client, workspace, project, tree):
        response = session_client.get(_list_url(workspace, project))

        assert "node_type" not in response.data[0], "默认路径的响应必须逐字不变"


@pytest.mark.contract
class TestChildPagesAreReachable:
    """两个既有缺口 —— 没入口建子页时看不见，一能建就立刻响。"""

    @pytest.mark.django_db
    def test_a_child_page_can_be_retrieved(self, session_client, workspace, project, tree):
        response = session_client.get(_detail_url(workspace, project, tree["d"]))

        assert response.status_code == status.HTTP_200_OK
        assert str(response.data["id"]) == str(tree["d"].id)

    @pytest.mark.django_db
    def test_creating_a_child_page_returns_201(self, session_client, workspace, project, tree, no_broker):
        response = session_client.post(
            _list_url(workspace, project),
            {"name": "新子页", "parent": str(tree["a"].id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        created = Page.objects.get(pk=response.data["id"])
        assert created.parent_id == tree["a"].id
