# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""岗位组的**名册**与**部署** —— 第三期设计 §3 / §4。

这两件事都照 ``block/buzz`` 的 ``apply_team_membership_delta`` 与
``AddTeamToChannelDialog`` 的扇出写，且都**只走显式增量**：

⚠️ **永远不要用 M2M 的 ``set()`` / ``remove()`` / ``clear()`` 改名册。** 反向 M2M 的描述符
走**目标模型的默认管理器**（``SoftDeletionManager``）、**会过滤软删的岗位** —— 于是
``set()`` 按「看得见的集合」整体替换名册、``remove()`` 把待删的 id 再筛一遍。两条的净效果
都是「软删岗位的 through 行动不了」，区别只在是**静默抹掉**还是**静默留下**：
抹掉会让紧接着的解绑把活着的成员踢出组，留下则让这个组**永远部署不了**（体检 409）而界面
修不好。只有 ``add()`` 不筛 —— 这份对称性的缺口是本模块唯一用 through 表直写的原因。
（两个方向相反的过滤行为钉死在 ``tests/unit/models/test_agent_group.py``；描述符那条
钉死在 ``tests/unit/views/test_agent_groups_api.py::test_a_roster_edit_can_clear_a_since_deleted_post``。）
"""

# Django imports
from django.db import IntegrityError

# Module imports
from plane.db.models import AgentDefinition, AgentMember

from .agent_identity import deploy


class RosterIncomplete(Exception):
    """名册里有已经删掉的岗位 —— 部署前必须挡住（buzz 是禁用部署按钮 + 红条）。"""

    def __init__(self, names):
        self.names = list(names)
        super().__init__(f"roster has {len(self.names)} missing post(s)")


def raw_roster_ids(group) -> set:
    """名册的**全部** through 行 id，**含软删的岗位**。

    不能走 ``group.definitions.values_list(...)`` —— 那条反向 M2M 走
    ``_default_manager``、**会过滤软删行**，于是「名册里有个已删的岗位」这个状态
    **永远看不到**，体检也就永远不触发。直接查 through 表是唯一能看见它的路。

    反向那份（``group.definitions``，**只看得见活着的岗位**）由序列化器的嵌套只读字段
    直接用 —— 编辑表单的勾选列表本来就只该列出活着的岗位，那是它的正确行为，不是漏洞。
    两处读的不是同一个集合，别互相「统一」。
    """
    through = type(group).definitions.through
    return set(
        through.objects.filter(agentgroup_id=group.pk).values_list("agentdefinition_id", flat=True)
    )


def _drop_from_roster(group, definition_ids) -> None:
    """从 through 表**直接**删行 —— 名册「减项」的唯一通路。

    ⚠️ **不能用 ``group.definitions.remove(...)``。** Django 的 ``_remove_items``
    （``django/db/models/fields/related_descriptors.py:1592-1610``）在目标模型的默认
    管理器**带过滤**时会先 ``target_model_qs.filter(pk__in=old_ids)`` 把待删的 id
    **再筛一遍**：``target_model_qs`` 是 ``super().get_queryset()``，即**目标模型的默认
    管理器** —— 也就是 ``SoftDeletionManager`` ⇒ **软删的岗位被筛掉 ⇒ 那一行 through
    行永远删不掉** ⇒ 名册编辑**静默空转**：组里于是永远留着一个死岗位，部署永远 409，
    而界面上没有任何东西能修它（勾选列表里也没有那个死岗位可摘）。

    ``clear()`` 走同一条路（``:1305``），``set()`` 会顺带走到 ``_remove_items`` ⇒ 两个
    同样中招。``add()`` 不筛（``:1516`` 直接用传入的 id），所以只有减项要绕过描述符。
    ``add('/'remove)`` 这个不对称**是本函数存在**的全部理由，别「统一」掉任何一半。
    """
    through = type(group).definitions.through
    through.objects.filter(
        agentgroup_id=group.pk, agentdefinition_id__in=list(definition_ids)
    ).delete()


def apply_roster_delta(*, group, previous_ids, definition_ids) -> tuple[int, int]:
    """把名册改成 ``definition_ids``，并按 buzz 的规则**只动该动的成员**。

    ``previous_ids`` 必须是**保存之前**抓的名册快照（``raw_roster_ids``），由调用方传入。
    先 ``save()`` 再读名册会让增删双双为空 ⇒ 整段静默变成空转，且没有测试会红（buzz 也是
    显式把 ``previous_persona_ids`` 传进 ``apply_team_membership_delta`` 的）。

    两条规则（buzz ``commands/teams/mod.rs``）：
    - **加**岗位 ⇒ 只有 ``group`` 为空的成员被绑上；**已属别组的一律不动**。
    - **删**岗位 ⇒ 只有绑在**本组**的成员被解绑；属别组的一律不动。

    返回 ``(bound, unbound)`` 供视图报告。两处都用 queryset ``.update()``（不过信号）。
    """
    added = set(definition_ids) - set(previous_ids)
    removed = set(previous_ids) - set(definition_ids)

    if added:
        group.definitions.add(*added)
    if removed:
        _drop_from_roster(group, removed)

    bound = 0
    unbound = 0
    if added:
        # **``group__isnull=True`` 是这条规则的全部要点** —— 少了它，改组名册会抢走
        # 别组的成员，把那条组的正文从它们的下一次运行里抹掉（buzz 的同名分支亦然）。
        bound = AgentMember.objects.filter(
            workspace_id=group.workspace_id,
            definition_id__in=added,
            group__isnull=True,
            deleted_at__isnull=True,
        ).update(group=group)
    if removed:
        unbound = AgentMember.objects.filter(
            workspace_id=group.workspace_id,
            definition_id__in=removed,
            group=group,
            deleted_at__isnull=True,
        ).update(group=None)

    return bound, unbound


def deploy_group(*, group, project, created_by_id) -> dict:
    """把整组岗位一次部署进 ``project``。设计 §3。

    罗盘没有 buzz 的 ``forceNewInstance``（一个岗位可属多组 ⇒ 铸多个实例），因为
    ``(project, definition)`` 的唯一约束在仲裁。所以第二次组撞上同一个岗位时，
    罗盘的答案是**拒绝并报告**，而不是把已部署成员静静地改挂到别组 ——
    buzz 自己也不抢绑定（``added`` 只回填未绑定的实例）。

    返回 ``{created, bound, unchanged, conflicts, failures}``。**部分成功是一等公民**
    （buzz：``Deployed N agents. M failed.``）。
    """
    roster = raw_roster_ids(group)
    live = set(AgentDefinition.objects.filter(pk__in=roster).values_list("id", flat=True))
    missing = roster - live
    if missing:
        names = list(
            AgentDefinition.all_objects.filter(pk__in=missing).values_list("name", flat=True)
        )
        raise RosterIncomplete(names)

    result = {"created": [], "bound": [], "unchanged": [], "conflicts": [], "failures": []}

    for definition in AgentDefinition.objects.filter(pk__in=roster).order_by("name"):
        member = AgentMember.objects.filter(
            project=project, definition=definition, deleted_at__isnull=True
        ).first()

        if member is None:
            try:
                deploy(definition=definition, project=project, created_by_id=created_by_id, group=group)
            except IntegrityError:
                # 与我们上面那次查询之间输了竞速 —— 唯一约束是仲裁者。当作冲突报出去。
                result["conflicts"].append({"definition_id": str(definition.id), "name": definition.name})
                continue
            result["created"].append({"definition_id": str(definition.id), "name": definition.name})
        elif member.group_id is None:
            AgentMember.objects.filter(pk=member.pk).update(group=group)
            result["bound"].append({"definition_id": str(definition.id), "name": definition.name})
        elif member.group_id == group.pk:
            result["unchanged"].append({"definition_id": str(definition.id), "name": definition.name})
        else:
            # 已属**别组** —— 绝不抢绑定（buzz 同款）。
            result["conflicts"].append({"definition_id": str(definition.id), "name": definition.name})

    return result


def unbind_members_of(group) -> int:
    """删组前把成员解绑。**不是**级联删除 —— 成员继续跑，只是不再带这组的正文。

    buzz 的删组是**拒绝式**（还有实例引用就删不掉），罗盘的视图守卫照抄了那一条，
    所以这条函数在 API 路径上够不着。它存在是为了让「组没了、绑定还指着它」这个
    中间态**不可表示** —— 一旦守卫哪天被绕过，也不会有成员的 ``group_id`` 指向坟头。
    """
    return AgentMember.objects.filter(group=group, deleted_at__isnull=True).update(group=None)
