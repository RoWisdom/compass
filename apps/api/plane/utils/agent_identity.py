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
from django.db import transaction
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


def deploy(*, definition, project, created_by_id, group=None):
    """把 ``definition`` 部署进 ``project``，返回成员行。

    ``group`` 是第三期加的：从岗位组部署时，成员的组绑定必须与那次 INSERT **同在一个
    事务**里（否则会留下一个「已部署但没绑组」的中间态，组正文静默不生效）。

    **不是幂等函数。** 第二次调用会在最后那次 ``AgentMember`` INSERT 上撞
    ``(project, definition)`` 的唯一约束并抛 ``IntegrityError`` —— 调用方负责预检、
    返回 409（``AgentMemberViewSet.create``）。唯一约束是仲裁者，不是护栏。

    整段必须在一个事务里：那次 INSERT **之前**已经写好了 bot 用户、工作区成员、
    项目成员三样东西。生产是 autocommit，不套 atomic 就没人替它们回滚 ⇒ 撞约束时
    留下一个**没有成员行的孤儿 bot 用户**（连带两条成员关系）。
    """
    with transaction.atomic():
        return _deploy(definition=definition, project=project, created_by_id=created_by_id, group=group)


def _deploy(*, definition, project, created_by_id, group=None):
    """``deploy`` 的事务体。别直接调 —— 它是半成品状态，往外可见要靠外面那层 atomic。"""
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
        group=group,
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


def resync_project_roles(definition):
    """把 ``definition`` 的**当前档位**重算到它在各项目里 bot 的 ``ProjectMember.role`` 上。

    这个模块管三件事，分工如下：``deploy`` **建**身份（bot 用户 + 工作区/项目成员 +
    service token），``retire`` **摘**身份（摘 ``ProjectMember`` + 废 token），
    ``resync_project_roles`` 则是**档位 → 角色**这条链的**唯一出口**。

    为什么必须有一个出口：沙箱模式是**现场**从 ``definition.tier`` 推的
    （``AgentMember.permission_mode``），但 bot 的公开 API 项目权限是**快照**在这行
    ``ProjectMember.role`` 上的。**任何**改写 ``definition.tier`` 的路径 —— 视图的
    ``partial_update``、管理命令 ``seed_agent_members``、将来的表单 —— 都必须写完之后
    调它一次；漏掉一处，那个 bot 的权限就停在部署时的旧档位上，与沙箱模式静默分叉
    （「可见的按钮不是唯一的栅栏」，管理命令也是一扇门）。

    **幂等**：不改档位时只是把每行重写成它本来就有的值，所以调用方不必先判「档位有没有真的变」。

    role 一律走 ``AgentMember.project_role`` 那条委托链（它读 ``definition.tier``），
    不在这重写 ``15 if … else 5`` —— 同一条规则存两份就是多一份。只动**活着的**成员行；
    ``select_related("definition")`` 是为了让 ``member.project_role`` 不再各打一次库。
    """
    for member in definition.members.filter(deleted_at__isnull=True).select_related("definition"):
        ProjectMember.objects.filter(
            project_id=member.project_id,
            member_id=member.bot_user_id,
        ).update(role=member.project_role)
