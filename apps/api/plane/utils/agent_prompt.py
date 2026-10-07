# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""把一次运行的「任务正文」拼出来。

分层照设计 §3②：**易变的东西进正文，稳定的契约进 profile**。所以这里只有
说明书 + 挂载技能 + 这张卡 + 岛路径（+ 第二轮的计划原文），**没有**输出契约 ——
那份契约在 ``~/.dsh/profiles/compass-ai/cordis.patch.yml`` 的 persona 里。
"""

# Module imports
from plane.utils.agent_run import skill_body

#: 第一轮末尾的免责句。闸的一半就靠这句话（另一半是 Plane 侧的 `plan` 状态位）。
PLAN_DISCLAIMER = (
    "**这一轮你只输出计划，不要动手**：不要建卡、不要改字段、不要写文件、不要发评论。"
    "把计划作为你这次的**最终回答**直接输出（就输出在 stdout 上，Plane 会替你贴到卡片上）。"
    "计划里要写清：打算做什么、会动哪几张卡、动完是什么样、以及你**不打算**做什么。"
)


def member_handbook(member) -> str:
    """岗位说明书 + 挂载技能的正文，顺序拼接（设计 §8：挂载 = 拼接）。

    **现场读定义，不读副本** —— 成员行上没有说明书（第二期设计 §2）。
    """
    definition = member.definition
    parts = [f"# 你是谁：{definition.name}", "", (definition.instructions or "").strip()]
    for name in definition.skills or []:
        body = skill_body(name)
        if body:
            parts += ["", f"# 技能：{name}", "", body.strip()]
    return "\n".join(parts).strip()


def _issue_block(project, issue) -> str:
    identifier = f"{project.identifier}-{issue.sequence_id}"
    return "\n".join(
        [
            "# 这一次的卡片",
            "",
            f"- 标识：{identifier}",
            f"- 标题：{issue.name}",
            f"- 描述：{(issue.description_stripped or '（没有描述）').strip()}",
        ]
    )


def _island_block(island) -> str:
    return "\n".join(
        [
            "# 你的岛",
            "",
            f"你的工作目录是 `{island}`。它就是你唯一可写的范围。",
        ]
    )


def _compose(member, project, issue, island, tail: str) -> str:
    return "\n\n".join(
        [
            member_handbook(member),
            _island_block(island),
            _issue_block(project, issue),
            tail,
        ]
    )


def build_plan_prompt(*, member, project, issue, island) -> str:
    """丙档第一轮：只出计划。结果会被 Plane 贴成一条评论（作者是 bot）。"""
    return _compose(member, project, issue, island, PLAN_DISCLAIMER)


def build_execute_prompt(*, member, project, issue, island, plan) -> str:
    """丙档第二轮：把第一轮的**计划原文**拼进来，这次真的动手。

    选「拼 prompt」而不是 ``--resume``：headless 能不能 resume 尚未验证
    （设计 §4）。
    """
    tail = "\n".join(
        [
            "# 已批准的计划（原文）",
            "",
            plan.strip(),
            "",
            "# 现在",
            "",
            "**人已经批准了这份计划。按这份计划动手。** 如果你在执行中发现计划有错，"
            "不要自作主张改成别的 —— 停手，把分歧写成结论。",
        ]
    )
    return _compose(member, project, issue, island, tail)
