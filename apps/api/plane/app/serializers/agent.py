# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third-party imports
from rest_framework import serializers

# Module imports
from plane.db.models import AgentMember, AgentRun


class AgentMemberSerializer(serializers.ModelSerializer):
    """Read-only. Members are seeded by a management command (design §2).

    ``instructions`` and ``service_token`` are deliberately absent: the frontend
    has no business rendering a handbook it cannot edit, and it must never see a
    token.
    """

    class Meta:
        model = AgentMember
        fields = ["id", "name", "color", "tier", "is_active", "project_id"]
        read_only_fields = fields


class AgentRunSerializer(serializers.ModelSerializer):
    """Read-only view of a run. Creation goes through ``AgentRunViewSet.create``."""

    class Meta:
        model = AgentRun
        fields = [
            "id",
            "member_id",
            "issue_id",
            "trigger",
            "status",
            "session_ref",
            "artifacts",
            "exit_code",
            "error",
            "started_at",
            "finished_at",
            "created_at",
        ]
        read_only_fields = fields
