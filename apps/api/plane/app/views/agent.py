# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third-party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import AgentMemberSerializer, AgentRunSerializer
from plane.bgtasks.agent_run_task import run_agent_member
from plane.db.models import AGENT_UNFINISHED_STATUSES, AgentMember, AgentRun, Issue
from plane.utils.exception_logger import log_exception

from .base import BaseViewSet


class AgentMemberViewSet(BaseViewSet):
    serializer_class = AgentMemberSerializer
    model = AgentMember

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def list(self, request, slug, project_id):
        members = AgentMember.objects.filter(
            workspace__slug=slug, project_id=project_id, is_active=True
        ).order_by("created_at")
        return Response(AgentMemberSerializer(members, many=True).data, status=status.HTTP_200_OK)


class AgentRunViewSet(BaseViewSet):
    serializer_class = AgentRunSerializer
    model = AgentRun

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def list(self, request, slug, project_id):
        """This project's runs, newest first.

        ``BaseViewSet.get_queryset`` is ``self.model.objects.all()`` — unscoped — so
        leaving ``list`` to the inherited ``ModelViewSet`` would hand every
        authenticated user every ``AgentRun`` in the database, ``error``,
        ``artifacts`` and ``session_ref`` included.
        """
        runs = AgentRun.objects.filter(
            workspace__slug=slug, project_id=project_id
        ).order_by("-created_at")
        return Response(AgentRunSerializer(runs, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def create(self, request, slug, project_id):
        """Wake one member on one card. Design §3① + §5's single-run lock."""
        try:
            member = AgentMember.objects.filter(
                workspace__slug=slug, project_id=project_id,
                pk=request.data.get("member_id"), is_active=True,
            ).first()
            if member is None:
                return Response(
                    {"error": "No such active AI member in this project"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            issue = Issue.objects.filter(
                workspace__slug=slug, project_id=project_id, pk=request.data.get("issue_id")
            ).first()
            if issue is None:
                return Response({"error": "No such work item"}, status=status.HTTP_400_BAD_REQUEST)

            unfinished = AgentRun.objects.filter(
                project_id=project_id, issue_id=issue.id, status__in=AGENT_UNFINISHED_STATUSES
            ).order_by("-created_at")

            # A repeat of the *same* wake-up is not an error: hand back the run that
            # is already going, so a double click cannot stack two headless runs.
            existing = unfinished.filter(member=member).first()
            if existing is not None:
                return Response(AgentRunSerializer(existing).data, status=status.HTTP_200_OK)

            if unfinished.exists():
                return Response(
                    {"error": "This work item already has a running AI member; wait for it or approve its plan."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            run = AgentRun.objects.create(
                workspace_id=member.workspace_id,
                project_id=project_id,
                member=member,
                issue=issue,
                triggered_by_id=request.user.id,
                created_by_id=request.user.id,
            )
            run_agent_member.delay(str(run.id))
            return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            log_exception(e)
            return Response(
                {"error": "Could not wake the AI member"},
                status=status.HTTP_400_BAD_REQUEST,
            )
