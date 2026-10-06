# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import (
    AgentMember,
    AgentRun,
    AgentRunStatusEnum,
    AgentTierEnum,
    BotTypeEnum,
)


@pytest.mark.django_db
def test_agent_member_defaults_and_project_scope(create_user, workspace, project):
    member = AgentMember.objects.create(
        name="需求分析师",
        instructions="你负责把模糊需求问清楚。",
        tier=AgentTierEnum.READONLY.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user=create_user,
    )
    assert member.profile == "compass-ai"
    assert member.skills == []
    assert member.is_active is True
    assert AgentMember.objects.filter(project_id=project.id).count() == 1


@pytest.mark.django_db
def test_agent_run_defaults_to_pending_manual(create_user, workspace, project, create_issue):
    member = AgentMember.objects.create(
        name="任务拆解",
        tier=AgentTierEnum.LEDGER.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user=create_user,
    )
    run = AgentRun.objects.create(
        member=member,
        issue_id=create_issue.id,
        project_id=project.id,
        workspace_id=workspace.id,
    )
    assert run.status == AgentRunStatusEnum.PENDING.value
    assert run.trigger == "manual"
    assert run.plan == ""
    assert run.artifacts == []
    assert run.exit_code is None


def test_bot_type_enum_has_agent():
    assert BotTypeEnum.AGENT.value == "AGENT"
