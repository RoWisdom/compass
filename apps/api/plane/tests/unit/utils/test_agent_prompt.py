# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from pathlib import Path

from plane.utils.agent_prompt import build_execute_prompt, build_plan_prompt, member_handbook


class FakeDefinition:
    def __init__(self, **kwargs):
        self.name = "架构设计"
        self.instructions = "你负责把方案写清楚。"
        self.skills = []
        self.tier = "writer"
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeMember:
    """说明书/技能/档位住在 ``definition`` 上，成员行上什么都没有 ——
    运行通路现场读定义（第二期设计 §2），所以这里的替身也必须长成那个形状。"""

    def __init__(self, **kwargs):
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
