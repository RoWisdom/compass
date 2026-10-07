# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""把「岗位 + 项目」变成一行能跑的成员：bot 用户 + 工作区/项目成员 + service token。

从 ``seed_agent_members`` 里抽出来的（设计 §2：定义无 key、实例有 key）。
两条路径共用它：管理命令与 ``AgentMemberViewSet.create``。

**token 只在这里被创建、只被 Celery 任务读**；这个模块**不返回、不打印、不记日志**
任何 token 值。
"""

# Python imports
from uuid import uuid4

# Django imports
from django.contrib.auth.hashers import make_password
from django.utils import timezone

# Module imports
from plane.db.models import (
    AgentMember,
    AgentTierEnum,
    APIToken,
    BotTypeEnum,
    ProjectMember,
    User,
    WorkspaceMember,
)


def deploy(*, definition, project, created_by_id):
    """把 ``definition`` 部署进 ``project``，返回成员行。幂等。

    幂等键是 ``(project, definition)`` —— 与成员行上的唯一约束同一把锁。
    """
    workspace = project.workspace

    # 预生成成员 id，好让 bot 用户名由它派生：确定性、唯一、无长度风险
    # （``User.username`` 上限 150；slug+项目 uuid+岗位 uuid 拼起来会超）。
    member_id = uuid4()
    username = f"agent_{member_id.hex[:24]}"

    bot_user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            "display_name": definition.name,
            "first_name": definition.name,
            "last_name": "",
            "is_bot": True,
            "bot_type": BotTypeEnum.AGENT,
            "email": f"{username}@agents.local",
            "password": make_password(uuid4().hex),
            "is_password_autoset": True,
        },
    )

    # 工作区成员是 Plane 鉴权路径要有 workspace 上下文的必要条件；
    # 项目成员是公开 API 的 ProjectEntityPermission / ProjectLitePermission 放行的必要条件。
    WorkspaceMember.objects.get_or_create(
        workspace=workspace, member=bot_user, defaults={"role": 15, "company_role": ""}
    )
    ProjectMember.objects.get_or_create(
        project=project,
        member=bot_user,
        defaults={
            "workspace": workspace,
            "role": 15 if definition.tier == AgentTierEnum.LEDGER.value else 5,
        },
    )

    member = AgentMember.objects.create(
        id=member_id,
        workspace=workspace,
        project=project,
        definition=definition,
        bot_user=bot_user,
        is_active=True,
        created_by_id=created_by_id,
    )
    member.service_token = APIToken.objects.create(
        user=bot_user,
        user_type=1,  # Bot
        workspace=workspace,
        is_service=True,
        label=f"agent-{definition.name}",
        description=f"Service token for the AI member {definition.name}",
        expired_at=timezone.now() + timezone.timedelta(days=365),
        created_by_id=created_by_id,
    )
    member.save(update_fields=["service_token"])
    return member


def retire(member):
    """从项目里摘掉一个成员：摘 ``ProjectMember`` + 废 token。设计 §6②。

    **不动 bot 用户本身** —— 它是这个成员行的身份，而成员行已经被调用方软删了；
    留着用户是留一条审计线索，且删用户会连带 CASCADE 掉 ``AgentRun``。

    这一条是「可见的按钮不是唯一的栅栏」：只软删成员行、不摘项目身份，
    就等于按完「移除」还留着一扇能发评论的后门。
    """
    ProjectMember.objects.filter(project_id=member.project_id, member_id=member.bot_user_id).delete()

    token = member.service_token
    if token is not None:
        APIToken.objects.filter(pk=token.pk).update(
            is_active=False, expired_at=timezone.now()
        )
