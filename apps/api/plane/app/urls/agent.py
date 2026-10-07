# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import AgentMemberViewSet, AgentRunViewSet

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-members/",
        AgentMemberViewSet.as_view({"get": "list"}),
        name="agent-member",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/",
        AgentRunViewSet.as_view({"get": "list", "post": "create"}),
        name="agent-run",
    ),
]
