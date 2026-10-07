# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models

# Module imports
from .base import BaseModel
from .project import ProjectBaseModel


class AgentTierEnum(models.TextChoices):
    """能力档位 —— 分界线是「要不要动 Plane 的台账」。

    档位同时决定三样结构化字段：DSH 的沙箱模式（``readonly`` ⇒ ``read-only``，
    其余 ⇒ ``workspace-write``）、该 bot 用户入项目时的角色（``readonly`` / ``writer``
    ⇒ GUEST=5，``ledger`` ⇒ MEMBER=15），以及要不要过闸（只有 ``ledger`` 有闸）。
    见设计 §4。
    """

    READONLY = "readonly", "Read Only"
    WRITER = "writer", "Writer"
    LEDGER = "ledger", "Ledger"


class AgentRunStatusEnum(models.TextChoices):
    PENDING = "pending", "Pending"
    #: 丙档第一轮跑完了，计划评论贴出去了，等人回「批准」。
    AWAITING_APPROVAL = "awaiting_approval", "Awaiting Approval"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class AgentRunTriggerEnum(models.TextChoices):
    """第一期只有手动。枚举预留 mention / assignment / event / schedule。"""

    MANUAL = "manual", "Manual"


#: 「还没有结束」的状态集合 —— 一张卡上同时只允许一个。Task 10 的锁用它。
AGENT_UNFINISHED_STATUSES = (
    AgentRunStatusEnum.PENDING.value,
    AgentRunStatusEnum.AWAITING_APPROVAL.value,
    AgentRunStatusEnum.RUNNING.value,
)


class AgentDefinition(BaseModel):
    """一个**岗位** = 一个业务功能 = 一行可复用配置（设计 §2）。

    工作区级资产：同一个「需求分析」可以被任意多个项目加进去，说明书只有这一份。
    **注意继承的是 ``BaseModel`` 而不是 ``WorkspaceBaseModel``** —— 后者自带一个可空的
    ``project`` FK，而岗位按定义就不是项目级的（先例：``WorkspaceMember``）。
    """

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="agent_definitions")
    name = models.CharField(max_length=255)
    #: 只用于展示（名册副标题 / 岗位库卡片）。280 是 buzz 的上限，照抄。
    description = models.CharField(max_length=280, blank=True)
    instructions = models.TextField(blank=True)
    skills = models.JSONField(default=list, blank=True)
    tier = models.CharField(max_length=20, choices=AgentTierEnum.choices, default=AgentTierEnum.READONLY)
    model = models.CharField(max_length=255, blank=True)
    profile = models.CharField(max_length=255, default="compass-ai")
    web_access = models.BooleanField(default=False)
    trusted_urls = models.JSONField(default=list, blank=True)
    writable_paths = models.JSONField(default=list, blank=True)
    color = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Agent Definition"
        verbose_name_plural = "Agent Definitions"
        db_table = "agent_definitions"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_agent_definition_name_per_workspace",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.tier})"


class AgentMember(ProjectBaseModel):
    """一个 AI 成员 = 把一个**岗位**加进一个项目（设计 §2）。

    **这一行是「部署回执」**：bot 用户与 service token 挂在这儿，因为它们属于
    「这个岗位在这个项目里」这一次部署，不属于岗位本身（buzz 同形：定义无 key、
    实例有 key）。说明书/技能/档位**不在这一行** —— 它们属于 ``definition``，
    运行时现场解析，所以改一次岗位所有项目一起变。
    """

    definition = models.ForeignKey(
        "db.AgentDefinition",
        on_delete=models.PROTECT,
        related_name="members",
    )
    is_active = models.BooleanField(default=True)
    bot_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_memberships",
    )
    service_token = models.ForeignKey(
        "db.APIToken",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_members",
    )

    class Meta:
        verbose_name = "Agent Member"
        verbose_name_plural = "Agent Members"
        db_table = "agent_members"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "definition"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_agent_member_definition_per_project",
            )
        ]

    def __str__(self):
        return f"{self.definition_id}@{self.project_id}"

    @property
    def permission_mode(self):
        """档位 → DSH 的沙箱模式。见设计 §6 一的结论 1。

        **委托，不重新推导**：Celery 任务注入 ``DSH_PERMISSION_MODE`` 用的是
        ``plane.utils.agent_run.permission_mode_for_tier``，同一条规则存两份就是多一份。
        """
        from plane.utils.agent_run import permission_mode_for_tier

        return permission_mode_for_tier(self.definition.tier)

    @property
    def project_role(self):
        """档位 → 该 bot 用户入项目时的角色数值。"""
        return 15 if self.definition.tier == AgentTierEnum.LEDGER.value else 5


class AgentRun(ProjectBaseModel):
    """一次运行 = 一行记录。可查、可中断、可重跑（设计 §1 不变式 2）。"""

    member = models.ForeignKey("db.AgentMember", on_delete=models.CASCADE, related_name="runs")
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="agent_runs")
    trigger = models.CharField(
        max_length=20, choices=AgentRunTriggerEnum.choices, default=AgentRunTriggerEnum.MANUAL
    )
    status = models.CharField(
        max_length=20, choices=AgentRunStatusEnum.choices, default=AgentRunStatusEnum.PENDING
    )
    #: 丙档第一轮的计划原文。**非空即表示已过闸** —— 第二轮就是「有 plan 的那个 run」。
    #: 这是整条闸机制的唯一状态位，见设计 §4「闸 = 两次 headless」。
    plan = models.TextField(blank=True)
    #: DSH 会话目录名（``session-<uuid>``）。跑前跑后 diff ``~/.dsh/sessions/`` 得到。
    session_ref = models.CharField(max_length=255, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_agent_runs",
    )
    #: 跑前跑后 diff 岛目录得到的相对路径清单 —— 不信模型的自我报告（设计 §3）。
    artifacts = models.JSONField(default=list, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Agent Run"
        verbose_name_plural = "Agent Runs"
        db_table = "agent_runs"
        ordering = ("-created_at",)
        constraints = [
            # 「一张卡同时只有一个未完成的运行」（设计 §5）。这条**不是**优化，是正确性：
            # ``create`` 的 check-then-create 中间有个窗口，两个并发 POST 都能看到「没有未完成的
            # run」然后各建一行。索引把这个窗口关死在 DB 上 —— 输家拿到 IntegrityError。
            # 条件里的 ``deleted_at__isnull=True`` 与 ``AgentMember`` 那条同名约束保持一致：软删的行
            # 不该继续占着锁。
            models.UniqueConstraint(
                fields=["issue"],
                condition=models.Q(status__in=AGENT_UNFINISHED_STATUSES, deleted_at__isnull=True),
                name="unique_unfinished_agent_run_per_issue",
            )
        ]

    def __str__(self):
        return f"{self.member_id}@{self.issue_id} [{self.status}]"
