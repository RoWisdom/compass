# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""第三期「岗位组」的模型层契约。

这个文件里最要紧的是**软删在前向 / 反向两个方向上行为相反**这件事 ——
它不是理论，是两条相反的 Django 描述符路径，且其中一条会让组正文静默挂到一个
已经删掉的组上。落笔实现 ``_group_block`` 之前先在这里钉死。
"""

import pytest
from django.db import IntegrityError

from plane.db.models import (
    AgentDefinition,
    AgentGroup,
    AgentMember,
    AgentTierEnum,
)


def _definition(workspace, name="需求分析师"):
    return AgentDefinition.objects.create(
        workspace_id=workspace.id,
        name=name,
        instructions="你负责把模糊需求问清楚。",
        tier=AgentTierEnum.READONLY.value,
    )


def _group(workspace, name="三人小组", instructions="先对齐再动手。"):
    return AgentGroup.objects.create(
        workspace_id=workspace.id,
        name=name,
        instructions=instructions,
    )


def _member(workspace, project, create_user, definition, group=None):
    return AgentMember.objects.create(
        definition=definition,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user=create_user,
        group=group,
    )


@pytest.mark.django_db
def test_group_defaults(workspace):
    group = AgentGroup.objects.create(workspace_id=workspace.id, name="三人小组")
    assert group.description == ""
    assert group.instructions == ""
    assert group.definitions.count() == 0  # 空名册是合法的


@pytest.mark.django_db
def test_group_name_is_unique_per_workspace_while_live(workspace):
    _group(workspace)
    with pytest.raises(IntegrityError):
        _group(workspace)


@pytest.mark.django_db
def test_group_name_can_be_reused_after_soft_delete(workspace):
    first = _group(workspace)
    # 走 queryset 形式：实例形式会触发软删级联任务（见 views/agent.py 的 destroy 注释）。
    AgentGroup.objects.filter(pk=first.pk).delete()
    assert AgentGroup.objects.filter(name="三人小组").count() == 0
    # 条件唯一约束只约束活着的行，名字应当可以复用。
    assert _group(workspace).name == "三人小组"


# ---------------------------------------------------------------------------
# 🔴 前向 FK 会取回软删行 —— _group_block 必须自己判 deleted_at
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_member_group_returns_the_soft_deleted_group(create_user, workspace, project):
    """前向 FK 走 ``_base_manager``（本仓没有任何 base_manager_name 覆盖），
    所以它**不**过滤软删 —— ``member.group`` 会把已经删掉的组原样交出来。

    这条测试是 ``_group_block`` 里那句 ``group.deleted_at is not None`` 的依据。
    哪天后人的一个 Meta 改动让这里变成 ``None``，这条测试会红。
    """
    definition = _definition(workspace)
    group = _group(workspace)
    member = _member(workspace, project, create_user, definition, group=group)

    AgentGroup.objects.filter(pk=group.pk).delete()

    member.refresh_from_db()
    assert AgentMember.objects.get(pk=member.pk).group_id == group.pk  # 绑定还在
    assert member.group is not None, "前向 FK 开始过滤软删了？那 _group_block 可以简化"
    assert member.group.deleted_at is not None


@pytest.mark.django_db
def test_member_group_is_none_when_never_bound(create_user, workspace, project):
    """没绑组时 ``member.group`` 直接是 None，且**不**多打一次库。"""
    member = _member(workspace, project, create_user, _definition(workspace))
    assert member.group_id is None
    assert member.group is None


# ---------------------------------------------------------------------------
# 🔴 反向 FK 与 M2M 相反：它们**会**过滤软删
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_group_definitions_hides_a_soft_deleted_definition(create_user, workspace, project):
    """``group.definitions`` 走 ``_default_manager`` ⇒ 看不见软删的岗位。

    后果（本期设计的直接约束）：名册体检**不能**用它写，否则检查永不触发；
    名册编辑**绝不能**用 M2M 的 ``set()``，否则软删岗位的 through 行被静默丢掉。
    """
    definition = _definition(workspace)
    group = _group(workspace)
    group.definitions.add(definition)
    assert group.definitions.count() == 1

    AgentDefinition.objects.filter(pk=definition.pk).delete()

    assert group.definitions.count() == 0, "反向 M2M 开始返回软删行了？设计要重来"
    # through 行本身还在 —— 这就是 set() 会静默抹掉的那一行。
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM agent_groups_definitions WHERE agentgroup_id = %s",
            [str(group.pk)],
        )
        assert cur.fetchone()[0] == 1


@pytest.mark.django_db
def test_group_members_hides_a_soft_deleted_member(create_user, workspace, project):
    definition = _definition(workspace)
    group = _group(workspace)
    member = _member(workspace, project, create_user, definition, group=group)
    assert group.members.count() == 1

    AgentMember.objects.filter(pk=member.pk).delete()

    assert group.members.count() == 0


@pytest.mark.django_db
def test_definition_groups_reverse_hides_a_soft_deleted_group(workspace):
    """``definition.groups`` 也过滤软删 ⇒ 岗位在**已删组**的名册里时仍可被删除。
    这正是合并守卫（T7）想要的行为。"""
    definition = _definition(workspace)
    group = _group(workspace)
    group.definitions.add(definition)
    assert definition.groups.count() == 1

    AgentGroup.objects.filter(pk=group.pk).delete()

    assert definition.groups.count() == 0
