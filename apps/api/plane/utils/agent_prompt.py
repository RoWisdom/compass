# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""把一次运行的「任务正文」拼出来。

分层照设计 §3②：**易变的东西进正文，稳定的契约进 profile**。所以这里只有
说明书 + 挂载技能 + **岗位组正文** + 这张卡 + 岛路径（+ 第二轮的计划原文），
**没有**输出契约 —— 那份契约在 ``~/.dsh/profiles/compass-ai/cordis.patch.yml`` 的 persona 里。

正文的层序（第三期加了「组」这一层，照 buzz）：
``岗位说明书/技能`` → ``组级正文`` → ``岛`` → ``这张卡`` → ``尾巴``。
buzz 的原序是 ``<agent-instructions>``（岗位）→ ``<team-instructions>``（组）——
**组从属于岗位**，所以排在后面。
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


def _group_block(member) -> str:
    """组级正文 —— 照 buzz 的 ``<team-instructions>``：**排在岗位正文之后**，单独一节。

    这是第三期唯一的**新正文层**。buzz 的层序是
    ``<agent-instructions>``（岗位）→ ``<team-instructions>``（组），组从属于岗位，
    所以这里也接在 ``member_handbook`` 后面。

    两条照抄 buzz 的语义：
    - **空/纯空白 ⇒ 整节不出**（buzz ``spawn_snapshot.rs`` 是 ``trim()`` 后判空）。
    - **现场解析、不存副本**：只读绑定，所以改一次组正文，所有已部署成员的下一次运行即生效。

    **必须自己判 ``deleted_at``**：前向 FK 走 ``_base_manager``，本仓没有任何
    ``base_manager_name`` 覆盖 ⇒ ``member.group`` 会把**已软删的组**原样交出来
    （钉死在 ``tests/unit/models/test_agent_group.py::test_member_group_returns_the_soft_deleted_group``）。
    少这一句，删掉的组的正文会继续注入。
    """
    group = member.group
    if group is None or group.deleted_at is not None:
        return ""
    body = (group.instructions or "").strip()
    if not body:
        return ""
    return f"# 你所在的岗位组：{group.name}\n\n{body}"


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
    """拼正文。**过滤空节** —— 组正文为空/组被软删时整节不出现，
    而不是留下一个空行（buzz 也是先过滤再拼，见 ``StandingContext::sections``）。"""
    blocks = [
        member_handbook(member),
        _group_block(member),
        _island_block(island),
        _issue_block(project, issue),
        tail,
    ]
    return "\n\n".join(block for block in blocks if block)


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
