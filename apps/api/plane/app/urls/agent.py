# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    AgentDefinitionViewSet,
    AgentGroupViewSet,
    AgentMemberViewSet,
    AgentRunViewSet,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/agent-definitions/",
        AgentDefinitionViewSet.as_view({"get": "list", "post": "create"}),
        name="agent-definition",
    ),
    path(
        "workspaces/<str:slug>/agent-definitions/<uuid:pk>/",
        AgentDefinitionViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
        ),
        name="agent-definition-detail",
    ),
    path(
        "workspaces/<str:slug>/agent-groups/",
        AgentGroupViewSet.as_view({"get": "list", "post": "create"}),
        name="agent-group",
    ),
    path(
        "workspaces/<str:slug>/agent-groups/<uuid:pk>/",
        AgentGroupViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
        ),
        name="agent-group-detail",
    ),
    # 部署路由是**项目内**动作，所以 kwarg 必须字面叫 ``project_id`` —— `allow_permission`
    # 默认 `level="PROJECT"` 时会无条件读 `kwargs["project_id"]`（base.py:56）。
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-groups/<uuid:pk>/deploy/",
        AgentGroupViewSet.as_view({"post": "deploy"}),
        name="agent-group-deploy",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-members/",
        AgentMemberViewSet.as_view({"get": "list", "post": "create"}),
        name="agent-member",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-members/<uuid:pk>/",
        AgentMemberViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="agent-member-detail",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/",
        AgentRunViewSet.as_view({"get": "list", "post": "create"}),
        name="agent-run",
    ),
]
