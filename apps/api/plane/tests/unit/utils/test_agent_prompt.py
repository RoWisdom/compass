# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from pathlib import Path

from plane.utils.agent_prompt import build_execute_prompt, build_plan_prompt, member_handbook


class FakeDefinition:
    """``deleted_at`` 默认是 ``None``：真实的 ``member.definition`` 是前向 FK、不过滤软删，
    所以替身也必须长着一个可读的 ``deleted_at``（少它就是 ``AttributeError``）。"""

    def __init__(self, **kwargs):
        self.name = "架构设计"
        self.instructions = "你负责把方案写清楚。"
        self.skills = []
        self.tier = "writer"
        self.deleted_at = None
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeGroup:
    """第三期：成员可以属于一个「岗位组」，组只贡献 name + instructions + deleted_at。
    真实模型上 ``member.group`` 走前向 FK（不过滤软删），所以替身也照那个形状长。"""

    def __init__(self, name="三人小组", instructions="先对齐再动手。", deleted_at=None):
        self.name = name
        self.instructions = instructions
        self.deleted_at = deleted_at


class FakeMember:
    """说明书/技能/档位住在 ``definition`` 上，成员行上什么都没有 ——
    运行通路现场读定义（第二期设计 §2），所以这里的替身也必须长成那个形状。

    ``group`` 是第三期加的：**成员行上一个可空 FK**，默认为空。"""

    def __init__(self, group=None, **kwargs):
        self.group = group
        self.definition = FakeDefinition(**kwargs)


class FakeProject:
    id = "11111111-1111-1111-1111-111111111111"
    name = "找料系统"
    identifier = "ZL"
    workspace_id = "22222222-2222-2222-2222-222222222222"


class FakeIssue:
    id = "33333333-3333-3333-3333-333333333333"
    name = "把推荐结果去重"
    description_stripped = "同一批料重复出现。"
    sequence_id = 42


def test_handbook_concatenates_instructions_then_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    skill = tmp_path / "skills" / "先看后写" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("先读完整张卡再动手。", encoding="utf-8")

    member = FakeMember(skills=["先看后写", "不存在"])
    handbook = member_handbook(member)
    assert "你负责把方案写清楚。" in handbook
    assert "先读完整张卡再动手。" in handbook
    # 挂不上的技能不产生空标题
    assert "不存在" not in handbook


def test_plan_prompt_carries_identifier_island_and_no_action_disclaimer(tmp_path):
    prompt = build_plan_prompt(
        member=FakeMember(tier="ledger"),
        project=FakeProject(),
        issue=FakeIssue(),
        island=Path("/tmp/island"),
    )
    assert "ZL-42" in prompt
    assert "把推荐结果去重" in prompt
    assert "/tmp/island" in prompt
    assert "只输出计划" in prompt
    # 计划轮不该出现「按这份计划动手」
    assert "按这份计划动手" not in prompt


def test_execute_prompt_embeds_the_plan_verbatim(tmp_path):
    plan = "1. 建子卡 A\n2. 建子卡 B\n3. 指派给 jeff"
    prompt = build_execute_prompt(
        member=FakeMember(tier="ledger"),
        project=FakeProject(),
        issue=FakeIssue(),
        island=Path("/tmp/island"),
        plan=plan,
    )
    assert plan in prompt
    assert "按这份计划动手" in prompt


# ---------------------------------------------------------------------------
# 组级正文的四种状态（第三期）
# ---------------------------------------------------------------------------


def test_group_text_lands_after_the_post_text(tmp_path):
    """层序必须是「岗位正文 → 组正文」，照 buzz 的
    ``<agent-instructions>`` → ``<team-instructions>``。"""
    member = FakeMember(group=FakeGroup())
    prompt = build_plan_prompt(
        member=member,
        project=FakeProject(),
        issue=FakeIssue(),
        island=Path("/tmp/island"),
    )
    assert "你所在的岗位组：三人小组" in prompt
    assert "先对齐再动手。" in prompt
    # 组正文排在岗位正文之后 —— 这条顺序就是 buzz 的契约，写反了就等于组压过岗位。
    assert prompt.index("你负责把方案写清楚。") < prompt.index("先对齐再动手。")


def test_no_group_leaves_no_section(tmp_path):
    prompt = build_plan_prompt(
        member=FakeMember(),  # group=None
        project=FakeProject(),
        issue=FakeIssue(),
        island=Path("/tmp/island"),
    )
    assert "你所在的岗位组" not in prompt


def test_blank_group_instructions_leave_no_section(tmp_path):
    """空/纯空白 ⇒ 整节不出，**不是**渲染一个空标题（buzz trim 后判空）。"""
    member = FakeMember(group=FakeGroup(instructions="   \n  "))
    prompt = build_plan_prompt(
        member=member,
        project=FakeProject(),
        issue=FakeIssue(),
        island=Path("/tmp/island"),
    )
    assert "你所在的岗位组" not in prompt
    assert "三人小组" not in prompt


def test_soft_deleted_group_leaves_no_section(tmp_path):
    """前向 FK 会把软删的组交出来（见 models/test_agent_group.py 的钉死测试），
    所以这里必须自己判 deleted_at —— 少这一句，删掉的组的正文会继续注入。"""
    member = FakeMember(group=FakeGroup(deleted_at=object()))
    prompt = build_plan_prompt(
        member=member,
        project=FakeProject(),
        issue=FakeIssue(),
        island=Path("/tmp/island"),
    )
    assert "你所在的岗位组" not in prompt
    assert "先对齐再动手。" not in prompt


def test_soft_deleted_post_leaves_no_handbook(tmp_path):
    """同一条前向 FK，同一个坑：``member.definition`` 也会把软删的岗位交出来。

    岗位被删之后，成员若还拿着**旧说明书**跑，就是一次「它为什么还在做这件事」的
    静默失义 —— 与组正文那条一致：整节不出，而不是渲染一个没有身份的空标题。
    这一态今天被 destroy 守卫挡在门外（是纵深防御），但层内的对称性必须成立。
    """
    member = FakeMember(deleted_at=object())
    prompt = build_plan_prompt(
        member=member,
        project=FakeProject(),
        issue=FakeIssue(),
        island=Path("/tmp/island"),
    )
    assert "你是谁" not in prompt
    assert "你负责把方案写清楚。" not in prompt
    # 岗位节没了，其余各节照旧 —— 空串是被 `_compose` 过滤掉的，不是把整篇弄塌
    assert "ZL-42" in prompt
    assert "/tmp/island" in prompt
    assert member_handbook(member) == ""
