# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.core.management import call_command

from plane.db.models import (
    APIToken,
    AgentDefinition,
    AgentMember,
    AgentTierEnum,
    ProjectMember,
    User,
    WorkspaceMember,
)

#: Keyed by the **post** name (``definition.name``) — 岗位是工作区级的资产，成员行只是把它加进项目。
EXPECTED = {
    "需求分析": (AgentTierEnum.READONLY.value, 5),
    "架构设计": (AgentTierEnum.WRITER.value, 5),
    "任务拆解": (AgentTierEnum.LEDGER.value, 15),
}


@pytest.mark.django_db
def test_seed_creates_three_members_with_bot_users_and_tokens(workspace, project):
    call_command("seed_agent_members", workspace=workspace.slug, project=str(project.id))

    members = AgentMember.objects.filter(project_id=project.id).order_by("definition__name")
    assert members.count() == 3
    # 岗位是**工作区级**的：三个成员背后正好三行岗位，且不挂在项目上
    assert AgentDefinition.objects.filter(workspace_id=workspace.id).count() == 3
    for member in members:
        tier, role = EXPECTED[member.definition.name]
        assert member.definition.tier == tier
        assert member.definition.profile == "compass-ai"
        assert member.bot_user.is_bot is True
        assert member.bot_user.bot_type == "AGENT"
        assert member.service_token is not None
        assert member.service_token.is_service is True
        assert member.service_token.workspace_id == workspace.id
        # 档位落到项目角色 —— 甲/乙 是 GUEST，丙 是 MEMBER（偏离 2）
        assert ProjectMember.objects.get(
            project_id=project.id, member_id=member.bot_user_id
        ).role == role
        # bot 用户也必须在工作区里，否则 APIKeyAuthentication 之外的路径会缺上下文
        assert WorkspaceMember.objects.filter(
            workspace_id=workspace.id, member_id=member.bot_user_id
        ).exists()


@pytest.mark.django_db
def test_seed_is_idempotent(workspace, project):
    call_command("seed_agent_members", workspace=workspace.slug, project=str(project.id))
    call_command("seed_agent_members", workspace=workspace.slug, project=str(project.id))

    assert AgentMember.objects.filter(project_id=project.id).count() == 3
    assert User.objects.filter(is_bot=True, bot_type="AGENT").count() == 3
    assert APIToken.objects.filter(is_service=True).count() == 3


@pytest.mark.django_db
def test_re_running_updates_the_post_but_not_the_membership(workspace, project):
    """第一期那条坑的回归测试：seed 重跑**不该**动成员行。

    岗位是工作区级的、成员行是项目级的，所以重跑只在**岗位**那行上重写说明书；
    成员行（连同它的 bot 用户与 token）必须原样不动。
    """
    call_command("seed_agent_members", workspace=workspace.slug, project=str(project.id))
    member = AgentMember.objects.get(project=project, definition__name="需求分析")
    member_id, definition_id = member.id, member.definition_id

    call_command("seed_agent_members", workspace=workspace.slug, project=str(project.id))

    after = AgentMember.objects.get(project=project, definition__name="需求分析")
    assert after.id == member_id
    assert after.definition_id == definition_id
    assert AgentMember.objects.filter(project=project).count() == 3
    # 岗位也没被复制出第二行 —— 重跑命中同一行
    assert AgentDefinition.objects.filter(workspace_id=workspace.id).count() == 3


@pytest.mark.django_db
def test_show_tokens_prints_the_current_token_on_a_rerun(workspace, project, capsys):
    """``--show-tokens`` 必须给出**当前** token，而不是只在首次 seed 那次才有值。

    回归：它原先读的是 **bot 用户**的 ``get_or_create`` 返回值 ⇒ 第二次跑（bot 用户
    已存在）只打印 ``(existing)``。而「先跑一次 seed、再加开关跑第二次、把 token 抄给
    自己」正是 brief Step 5 规定的取 token 姿势 —— 那一步会静默失效。
    """
    call_command("seed_agent_members", workspace=workspace.slug, project=str(project.id))
    capsys.readouterr()  # 丢掉第一次的输出

    call_command("seed_agent_members", workspace=workspace.slug, project=str(project.id), show_tokens=True)
    out = capsys.readouterr().out

    assert "(existing)" not in out
    for member in AgentMember.objects.filter(project_id=project.id):
        assert member.service_token.token in out
