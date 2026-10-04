# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""两个镜像根字段的 PATCH 契约与权限（罗盘：Workspace 目录配置）。

端点：`PATCH /api/workspaces/<slug>/` → `WorkSpaceViewSet.partial_update`
（`app/urls/workspace.py:56-62`）。

**PATCH 有两道闸，净效果是 ADMIN-only。**（首轮把这个端点误测成「MEMBER 也放行」，
只看了第一道；实测 MEMBER 拿 403，代码核实见下。）

1. DRF `permission_classes` = `WorkSpaceBasePermission`
   （`app/views/workspace/base.py:58`）→ PUT/PATCH 放行 `role__in=[Admin, Member]`
   （`app/permissions/workspace.py:33-39`）。**MEMBER 过这一道。**
2. 方法装饰器 `@allow_permission([ROLE.ADMIN], level="WORKSPACE")`
   （`app/views/workspace/base.py:173-174`）→ 要求 `WorkspaceMember.role == 20`
   且 `is_active=True`（`app/permissions/base.py:19-51`，`ROLE.ADMIN = 20`）。
   **MEMBER(15) 不过 ⇒ 403。**

所以 **MEMBER 与 GUEST 都拿 403，只有 ADMIN 拿 200** —— 与设计文档 §2.4 的
ADMIN-only 一致，也与前端口径一致（`workspace-details.tsx:126` 每字段
`disabled={!isAdmin}`）。
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import User, WorkspaceMember


def _workspace_url(slug: str) -> str:
    return f"/api/workspaces/{slug}/"


def _make_user(email: str) -> User:
    local_part = email.split("@")[0]
    user = User.objects.create(email=email, username=local_part, first_name=local_part)
    user.set_password("test-password")
    user.save()
    return user


def _client_for(workspace, user, *, ws_role: int) -> APIClient:
    """把 ``user`` 加成工作区成员，返回一个已登录它的 client。"""
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=ws_role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceMarkdownPathsAPI:
    def test_admin_can_set_and_read_back_both_paths(self, session_client, workspace):
        """本功能最核心的一条：两个字段存得进、读得回。"""
        response = session_client.patch(
            _workspace_url(workspace.slug),
            {"project_markdown_path": "~/projects-x", "wiki_markdown_path": "/srv/vault/3-Wiki"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        workspace.refresh_from_db()
        assert workspace.project_markdown_path == "~/projects-x"
        assert workspace.wiki_markdown_path == "/srv/vault/3-Wiki"

    def test_empty_string_clears(self, session_client, workspace):
        """清空输入框后前端会送 ""，服务端必须接受（等价于回落默认）。"""
        session_client.patch(
            _workspace_url(workspace.slug), {"wiki_markdown_path": "/srv/vault/3-Wiki"}, format="json"
        )
        response = session_client.patch(_workspace_url(workspace.slug), {"wiki_markdown_path": ""}, format="json")
        assert response.status_code == status.HTTP_200_OK
        workspace.refresh_from_db()
        assert workspace.wiki_markdown_path == ""

    def test_member_gets_403_from_the_view_decorator(self, workspace):
        """**MEMBER 拿 403**，尽管 DRF 的 `WorkSpaceBasePermission` 放行 Member。

        拦住它的是第二道闸：`partial_update` 上的
        `@allow_permission([ROLE.ADMIN], level="WORKSPACE")`。见模块 docstring。
        """
        member = _make_user("paths-member@plane.so")
        client = _client_for(workspace, member, ws_role=15)
        response = client.patch(
            _workspace_url(workspace.slug), {"wiki_markdown_path": "/srv/vault/3-Wiki"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        workspace.refresh_from_db()
        assert workspace.wiki_markdown_path is None

    def test_guest_gets_403(self, workspace):
        guest = _make_user("paths-guest@plane.so")
        client = _client_for(workspace, guest, ws_role=5)
        response = client.patch(
            _workspace_url(workspace.slug), {"wiki_markdown_path": "/srv/vault/3-Wiki"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_relative_path_is_rejected(self, session_client, workspace):
        """相对路径 → 400，且**没有**写进库。"""
        response = session_client.patch(
            _workspace_url(workspace.slug), {"project_markdown_path": "relative/x"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        workspace.refresh_from_db()
        assert workspace.project_markdown_path is None

    def test_filesystem_root_is_rejected(self, session_client, workspace):
        response = session_client.patch(_workspace_url(workspace.slug), {"wiki_markdown_path": "/"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        workspace.refresh_from_db()
        assert workspace.wiki_markdown_path is None
