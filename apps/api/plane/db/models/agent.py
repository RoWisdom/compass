# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models

# Module imports
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


class AgentMember(ProjectBaseModel):
    """一个 AI 成员 = 一个业务功能 = 一行项目级配置 + 一行 bot 用户。

    说明书（``instructions``）是 **Plane 侧的真源**；DSH 只收到渲染好的正文。
    技能只存**名字**，本体在 DSH 文件系统 ``~/.dsh/skills/<name>/SKILL.md``（设计 §2 的
    第一处 YAGNI：不建技能表）。
    """

    name = models.CharField(max_length=255)
    color = models.CharField(max_length=255, blank=True)
    instructions = models.TextField(blank=True)
    skills = models.JSONField(default=list, blank=True)
    tier = models.CharField(max_length=20, choices=AgentTierEnum.choices, default=AgentTierEnum.READONLY)
    model = models.CharField(max_length=255, blank=True)
    profile = models.CharField(max_length=255, default="compass-ai")
    web_access = models.BooleanField(default=False)
    trusted_urls = models.JSONField(default=list, blank=True)
    # 第一期由 tier 推导（= 岛）。显式字段留给以后。
    writable_paths = models.JSONField(default=list, blank=True)
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
                fields=["project", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_agent_member_name_per_project",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.tier})"

    @property
    def permission_mode(self):
        """档位 → DSH 的沙箱模式。见设计 §6 一的结论 1。

        **委托，不重新推导**：Celery 任务注入 ``DSH_PERMISSION_MODE`` 用的是
        ``plane.utils.agent_run.permission_mode_for_tier``，同一条规则存两份就是多一份。
        """
        # Imported inside the property: ``plane.utils.agent_run`` imports nothing from
        # ``plane.db``, so this direction is safe, but keeping it lazy means model loading
        # never depends on the utils package.
        from plane.utils.agent_run import permission_mode_for_tier

        return permission_mode_for_tier(self.tier)

    @property
    def project_role(self):
        """档位 → 该 bot 用户入项目时的角色数值。见「偏离 2」。"""
        return 15 if self.tier == AgentTierEnum.LEDGER.value else 5


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

    def __str__(self):
        return f"{self.member_id}@{self.issue_id} [{self.status}]"
