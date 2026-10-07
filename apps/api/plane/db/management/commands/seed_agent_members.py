# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Seed a project's first AI members.

The handbook text lives **here**, in the command, because that is the thinnest
thing that works: changing a handbook means editing this file and re-running
(design §2 「成员怎么建」). A form can be drawn over the same fields later without
undoing anything.

Tokens are **not** printed unless you ask (``--show-tokens``): they are credentials,
and this repo's rule is that they never land in a log, a file, or a tool result.
"""

# Python imports
from uuid import uuid4

# Django imports
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.hashers import make_password
from django.utils import timezone

# Module imports
from plane.db.models import (
    AgentMember,
    AgentTierEnum,
    APIToken,
    BotTypeEnum,
    Project,
    ProjectMember,
    User,
    Workspace,
    WorkspaceMember,
)

READONLY = AgentTierEnum.READONLY.value
WRITER = AgentTierEnum.WRITER.value
LEDGER = AgentTierEnum.LEDGER.value

#: The first-phase roster. One business function, one member (design §4).
MEMBERS = [
    {
        "name": "需求分析",
        "tier": READONLY,
        "skills": [],
        "instructions": (
            "你负责把一张模糊的卡片问清楚。\n\n"
            "你要产出的东西：一段能让人一眼看懂「这件事到底要解决什么」的结论，"
            "以及一组**仍然没有被回答的问题**。\n\n"
            "判据：如果一句话里出现了「优化」「提升」「更」这类没有刻度的词，它就不是结论。\n\n"
            "边界：你不改任何卡片字段，也不建卡 —— 你的 token 也做不到。\n"
            "你只读，然后在卡片上留下一条评论。"
        ),
    },
    {
        "name": "架构设计",
        "tier": WRITER,
        "skills": [],
        "instructions": (
            "你负责在岛里写出一份能落地的方案稿。\n\n"
            "你会读卡片、读岛里已有的文件，然后写一个新的 `.md` 文件到一个**稳定的文件名**上 ——"
            "同一件事的第二版就覆盖同一个文件，好让版本可对比。\n\n"
            "判据：方案里每一个「怎么做」都要能追到一条「为什么不是另一种做法」。\n\n"
            "边界：你不改卡片字段、不建卡（token 做不到）。产物写进岛里，"
            "结论（含产物路径）用评论回到卡片上。"
        ),
    },
    {
        "name": "任务拆解",
        "tier": LEDGER,
        "skills": [],
        "instructions": (
            "你负责把一张卡拆成可执行的子任务。\n\n"
            "你**分两轮**工作：第一轮只输出计划（打算建哪几张子卡、各自标题与验收判据、"
            "设什么字段、指派给谁、以及不动什么）；人回「批准」之后，第二轮才真的动手。\n\n"
            "动手时你确实可以建子卡、改字段、指派 —— 你的 token 允许这些。\n\n"
            "边界有一条硬的：**你不能改卡片状态**。改状态由 Plane 的代码做，"
            "你的请求会被 API 直接拒掉。别去试，也别在结论里假装你改了。"
        ),
    },
]


class Command(BaseCommand):
    help = "Create the first-phase AI members of a project (bot user + service token + AgentMember)."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", required=True, help="Workspace slug")
        parser.add_argument("--project", required=True, help="Project id (uuid)")
        parser.add_argument(
            "--show-tokens",
            action="store_true",
            help="Print the service tokens. Off by default: they are credentials.",
        )

    def handle(self, *args, **options):
        workspace = Workspace.objects.filter(slug=options["workspace"]).first()
        if workspace is None:
            raise CommandError(f"No workspace with slug {options['workspace']!r}")
        project = Project.objects.filter(workspace=workspace, pk=options["project"]).first()
        if project is None:
            raise CommandError(f"No project {options['project']!r} in workspace {workspace.slug}")

        for spec in MEMBERS:
            member = self._seed_one(workspace=workspace, project=project, spec=spec)
            tokens = getattr(member, "_seeded_token", None)
            line = f"{member.name:8s} tier={member.tier:8s} bot={member.bot_user_id}"
            if options["show_tokens"]:
                line += f" token={tokens}"
            self.stdout.write(line)

    def _seed_one(self, *, workspace, project, spec):
        slot = f"{workspace.slug}-{project.id}-{spec['tier']}"
        username = f"agent_{slot}"
        bot_user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "display_name": spec["name"],
                "first_name": spec["name"],
                "last_name": "",
                "is_bot": True,
                "bot_type": BotTypeEnum.AGENT,
                "email": f"{username}@agents.local",
                "password": make_password(uuid4().hex),
                "is_password_autoset": True,
            },
        )

        # The bot has to be in the workspace for Plane's auth paths to have a
        # workspace context, and in the project for the public API's
        # ProjectEntityPermission / ProjectLitePermission to let it through at all.
        WorkspaceMember.objects.get_or_create(
            workspace=workspace, member=bot_user, defaults={"role": 15, "company_role": ""}
        )
        ProjectMember.objects.get_or_create(
            project=project,
            member=bot_user,
            defaults={"workspace": workspace, "role": 15 if spec["tier"] == LEDGER else 5},
        )

        member, _ = AgentMember.objects.update_or_create(
            project=project,
            name=spec["name"],
            defaults={
                "workspace": workspace,
                "tier": spec["tier"],
                "instructions": spec["instructions"],
                "skills": spec["skills"],
                "bot_user": bot_user,
                "is_active": True,
                "created_by_id": bot_user.id,
            },
        )

        if member.service_token_id is None:
            token = APIToken.objects.create(
                user=bot_user,
                user_type=1,  # Bot
                workspace=workspace,
                is_service=True,
                label=f"agent-{spec['name']}",
                description=f"Service token for the AI member {spec['name']}",
                # A service token with no expiry is the wrong default; the plan's
                # Phase 1 runs on a local dev box, so give it a long but finite life.
                expired_at=timezone.now() + timezone.timedelta(days=365),
                created_by_id=bot_user.id,
            )
            member.service_token = token
            member.save(update_fields=["service_token"])

        # ``--show-tokens`` echoes the *current* token, not only a freshly minted one.
        # The documented flow is: seed once, then re-run with the flag to copy the
        # tokens out. Gating this on "the bot user was just created" left that second
        # run printing "(existing)" — which is the flag's entire job, undone.
        member._seeded_token = member.service_token.token
        return member
