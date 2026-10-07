# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.core.management import call_command

from plane.db.models import APIToken, AgentMember, AgentTierEnum, ProjectMember, User, WorkspaceMember

EXPECTED = {
    "需求分析": (AgentTierEnum.READONLY.value, 5),
    "架构设计": (AgentTierEnum.WRITER.value, 5),
    "任务拆解": (AgentTierEnum.LEDGER.value, 15),
}


@pytest.mark.django_db
def test_seed_creates_three_members_with_bot_users_and_tokens(workspace, project):
    call_command("seed_agent_members", workspace=workspace.slug, project=str(project.id))

    members = AgentMember.objects.filter(project_id=project.id).order_by("name")
    assert members.count() == 3
    for member in members:
        tier, role = EXPECTED[member.name]
        assert member.tier == tier
        assert member.profile == "compass-ai"
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
