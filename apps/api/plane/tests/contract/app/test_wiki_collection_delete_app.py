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
