# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.db.models import ProtectedError

from plane.db.models import (
    AgentDefinition,
    AgentMember,
    AgentRun,
    AgentRunStatusEnum,
    AgentTierEnum,
    BotTypeEnum,
)


@pytest.mark.django_db
def test_agent_member_is_scoped_to_its_project(create_user, workspace, project):
    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id,
        name="需求分析师",
        instructions="你负责把模糊需求问清楚。",
        tier=AgentTierEnum.READONLY.value,
    )
    member = AgentMember.objects.create(
        definition=definition,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user=create_user,
    )
    assert member.is_active is True
    assert AgentMember.objects.filter(project_id=project.id).count() == 1


@pytest.mark.django_db
def test_agent_definition_defaults(workspace):
    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id,
        name="需求分析师",
        tier=AgentTierEnum.READONLY.value,
    )
    assert definition.profile == "compass-ai"
    assert definition.skills == []


@pytest.mark.django_db
def test_agent_definition_is_protected_while_referenced(create_user, workspace, project):
    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id,
        name="需求分析师",
        tier=AgentTierEnum.READONLY.value,
    )
    AgentMember.objects.create(
        definition=definition,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user=create_user,
    )
    # BaseModel.delete() 默认是软删（只写 deleted_at），不碰 FK 保护；这里要的是硬删。
    with pytest.raises(ProtectedError):
        definition.delete(soft=False)


@pytest.mark.django_db
def test_agent_run_defaults_to_pending_manual(create_user, workspace, project, create_issue):
    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id,
        name="任务拆解",
        tier=AgentTierEnum.LEDGER.value,
    )
    member = AgentMember.objects.create(
        definition=definition,
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
