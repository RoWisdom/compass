# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Seed the first AI posts, then add each of them to a project.

The handbook text lives **here**, in the command, because that is the thinnest
thing that works: changing a handbook means editing this file and re-running
(design §2 「成员怎么建」). A form can be drawn over the same fields later without
undoing anything.

The post is **workspace-level** and the membership is **project-level**, which is why
a re-run rewrites the handbook on the post and leaves the membership row alone
(design §2). The bot user + service token belong to the membership — that is the
``deploy`` call, not this file.

Tokens are **not** printed unless you ask (``--show-tokens``): they are credentials,
and this repo's rule is that they never land in a log, a file, or a tool result.
"""

# Django imports
from django.core.management.base import BaseCommand, CommandError

# Module imports
from plane.db.models import AgentDefinition, AgentMember, AgentTierEnum, Project, Workspace
from plane.utils.agent_identity import deploy, resync_project_roles

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
    help = (
        "Create the first-phase AI posts, then add each of them to the project "
        "(post + member + bot user + service token)."
    )

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
            definition = self._seed_definition(workspace=workspace, spec=spec)
            member = self._seed_membership(project=project, definition=definition)
            line = (
                f"{definition.name:8s} tier={definition.tier:8s} "
                f"bot={member.bot_user_id} post={definition.id}"
            )
            if options["show_tokens"]:
                # ``--show-tokens`` echoes the *current* token, not only a freshly minted one:
                # the documented flow is seed once, then re-run with the flag to copy them out.
                line += f" token={member.service_token.token}"
            self.stdout.write(line)

    def _seed_definition(self, *, workspace, spec):
        """岗位是**工作区级**的，所以重跑只更新说明书，不动成员行（第二期设计 §2）。

        这就是第一期那个坑的修法：那时的 ``update_or_create`` 会把说明书按文件正文
        重写，等于把线上改过的东西冲掉 —— 现在这是**岗位端**的显式行为，
        而且成员行那边**不会**再被 ``seed`` 碰。
        """
        definition, _ = AgentDefinition.objects.update_or_create(
            workspace=workspace,
            name=spec["name"],
            deleted_at__isnull=True,
            defaults={
                "tier": spec["tier"],
                "instructions": spec["instructions"],
                "skills": spec["skills"],
            },
        )
        # 上面那次 ``update_or_create`` 把档位按**文件正文**写回 —— 于是 seed 是**改 tier 的
        # 第二个写入方**（视图 ``partial_update`` 是第一个）。档位一改，bot 快照在
        # ``ProjectMember.role`` 上的项目权限就与现场从 ``tier`` 推的沙箱模式分叉，
        # 所以必须跟着走一遍这条链的**唯一出口**（``agent_identity.resync_project_roles``）。
        # 不判「档位有没有真的变」：那个 helper 幂等，且只有 3 行数据，加守卫只是多一处能写错的地方。
        resync_project_roles(definition)
        return definition

    def _seed_membership(self, *, project, definition):
        member = AgentMember.objects.filter(
            project=project, definition=definition, deleted_at__isnull=True
        ).first()
        if member is not None:
            return member
        return deploy(definition=definition, project=project, created_by_id=None)
