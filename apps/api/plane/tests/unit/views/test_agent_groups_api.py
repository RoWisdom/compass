# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""第三期「岗位组」的 API 契约：CRUD + 名册增量 + 部署四格。

三条最容易写错、也最容易静默失效的规则钉在这里：

1. **名册增量只动该动的成员**（buzz ``apply_team_membership_delta``）：加岗位只回填
   未绑定的、删岗位只解绑本组的，谁也不抢别组的绑定。
2. **差集必须用「保存之前的全部 through 行」算**（含软删岗位）。用反向 M2M 读出来的
   活岗位集去算，会让一次名册编辑**静默空转** —— 那个组从此永远部署不了，界面也修不好。
3. **部署是逐岗位扇出 + 部分成功一等公民**，四格（新建/绑定/不变/冲突）逐一可断言。
"""

import pytest

from plane.db.models import (
    AgentDefinition,
    AgentGroup,
    AgentMember,
    AgentTierEnum,
    Project,
)


@pytest.fixture
def no_worker(monkeypatch):
    """本测试内不允许有任何后台任务离开进程（测试没设 ``CELERY_TASK_ALWAYS_EAGER``）。"""
    monkeypatch.setattr("plane.app.views.agent.run_agent_member.delay", lambda run_id: None)
    monkeypatch.setattr(
        "plane.bgtasks.deletion_task.soft_delete_related_objects.delay", lambda *a, **k: None
    )


def _definition(workspace, name, instructions=""):
    return AgentDefinition.objects.create(
        workspace_id=workspace.id,
        name=name,
        instructions=instructions,
        tier=AgentTierEnum.READONLY.value,
    )


def _group(workspace, name="三人小组", instructions="先对齐再动手。"):
    return AgentGroup.objects.create(
        workspace_id=workspace.id, name=name, instructions=instructions
    )


def _member(workspace, project, bot_user, definition, group=None):
    return AgentMember.objects.create(
        definition=definition,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=bot_user.id,
        group=group,
    )


def _groups_url(workspace):
    return f"/api/workspaces/{workspace.slug}/agent-groups/"


def _deploy_url(workspace, project, group):
    return (
        f"/api/workspaces/{workspace.slug}/projects/{project.id}"
        f"/agent-groups/{group.id}/deploy/"
    )


def _other_project(workspace, create_user, identifier="OPJ", name="另一个项目"):
    return Project.objects.create(
        name=name, identifier=identifier, workspace=workspace, created_by=create_user
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_a_group_with_a_roster(session_client, workspace):
    a = _definition(workspace, "需求分析")
    b = _definition(workspace, "架构设计")

    response = session_client.post(
        _groups_url(workspace),
        {
            "name": "三人小组",
            "description": "从需求到拆解",
            "instructions": "先对齐再动手。",
            "definition_ids": [str(a.id), str(b.id)],
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["project_count"] == 0
    assert sorted(d["name"] for d in response.data["definitions"]) == ["架构设计", "需求分析"]
    # 名册真的落库了，不只是回显；写字段是 write_only，不该出现在响应里
    assert "definition_ids" not in response.data
    assert AgentGroup.objects.get(pk=response.data["id"]).definitions.count() == 2


@pytest.mark.django_db
def test_a_group_can_be_created_with_no_roster(session_client, workspace):
    """空名册是合法状态 —— 先建壳、再慢慢加岗位。"""
    response = session_client.post(_groups_url(workspace), {"name": "三人小组"}, format="json")
    assert response.status_code == 201
    assert response.data["definitions"] == []


@pytest.mark.django_db
def test_a_post_from_another_workspace_cannot_join_the_roster(
    session_client, workspace, create_user
):
    """名册是**工作区级**资产，跨工作区的 UUID 进得来（``ListField(UUIDField())``
    不做任何归属校验）。放进来等于给那个岗位开一条**静默生效的正文通道** ——
    部署时 ``_deploy`` 照样按 ``definition`` 铸 bot 身份，不看它在哪个工作区。
    """
    from plane.db.models import Workspace

    other = Workspace.objects.create(name="别的工作区", slug="other-ws", owner=create_user)
    foreign = AgentDefinition.objects.create(workspace_id=other.id, name="外面的岗位")

    response = session_client.post(
        _groups_url(workspace),
        {"name": "三人小组", "definition_ids": [str(foreign.id)]},
        format="json",
    )

    assert response.status_code == 400
    assert not AgentGroup.objects.filter(workspace_id=workspace.id).exists()


@pytest.mark.django_db
def test_a_soft_deleted_post_cannot_join_the_roster(session_client, workspace):
    """软删的岗位已经不该出现在任何名册里：写进去只会让下一次部署撞 ``RosterIncomplete``。"""
    gone = _definition(workspace, "需求分析")
    AgentDefinition.objects.filter(pk=gone.pk).delete()

    response = session_client.post(
        _groups_url(workspace),
        {"name": "三人小组", "definition_ids": [str(gone.id)]},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_two_groups_cannot_share_a_name(session_client, workspace):
    """0130 的条件唯一约束：同一工作区里组名唯一，且**必须是 409 而不是 500**
    （DRF 的 ``UniqueTogetherValidator`` 因 ``workspace_id`` 只读、无默认值而被整条跳过）。
    """
    url = _groups_url(workspace)
    assert session_client.post(url, {"name": "三人小组"}, format="json").status_code == 201

    second = session_client.post(url, {"name": "三人小组"}, format="json")
    assert second.status_code == 409
    assert "三人小组" in second.json()["error"]
    # 这句同时钉住 create 里那层 atomic（保存点）：去掉它，被 IntegrityError 染脏的连接
    # 会让这次查询抛 TransactionManagementError，而不是 1。
    assert AgentGroup.objects.filter(workspace_id=workspace.id, deleted_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_renaming_a_group_onto_an_existing_name_is_refused(session_client, workspace):
    url = _groups_url(workspace)
    session_client.post(url, {"name": "第一组"}, format="json")
    other = session_client.post(url, {"name": "第二组"}, format="json")

    response = session_client.patch(f"{url}{other.data['id']}/", {"name": "第一组"}, format="json")

    assert response.status_code == 409
    assert AgentGroup.objects.get(pk=other.data["id"]).name == "第二组"


@pytest.mark.django_db
def test_retrieve_carries_the_nested_roster(session_client, workspace):
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)

    response = session_client.get(f"{_groups_url(workspace)}{group.id}/")

    assert response.status_code == 200
    assert [d["name"] for d in response.data["definitions"]] == ["需求分析"]
    assert response.data["instructions"] == "先对齐再动手。"


# ---------------------------------------------------------------------------
# 名册增量（设计 §4 / buzz apply_team_membership_delta）
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_patching_the_name_alone_leaves_the_roster_alone(
    session_client, workspace, project, create_bot_user
):
    """**「没提名册」与「清空名册」是两件事。** 只改名字的 PATCH 不该顺手把名册清掉，
    更不该顺手把成员解绑 —— 那是一次没人要求过的破坏。
    """
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)
    member = _member(workspace, project, create_bot_user, a, group=group)

    response = session_client.patch(
        f"{_groups_url(workspace)}{group.id}/", {"name": "改了名的组"}, format="json"
    )

    assert response.status_code == 200
    assert group.definitions.count() == 1
    member.refresh_from_db()
    assert member.group_id == group.id


@pytest.mark.django_db
def test_an_empty_roster_list_clears_the_roster(
    session_client, workspace, project, create_bot_user
):
    """``definition_ids: []`` 才是「清空」—— 与上一个测试是同一个字段的两面。"""
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)
    member = _member(workspace, project, create_bot_user, a, group=group)

    response = session_client.patch(
        f"{_groups_url(workspace)}{group.id}/", {"definition_ids": []}, format="json"
    )

    assert response.status_code == 200
    assert group.definitions.count() == 0
    member.refresh_from_db()
    assert member.group_id is None


@pytest.mark.django_db
def test_adding_a_post_binds_only_unbound_members(
    session_client, workspace, project, create_bot_user
):
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    member = _member(workspace, project, create_bot_user, a)  # group=None

    response = session_client.patch(
        f"{_groups_url(workspace)}{group.id}/", {"definition_ids": [str(a.id)]}, format="json"
    )

    assert response.status_code == 200
    member.refresh_from_db()
    assert member.group_id == group.id


@pytest.mark.django_db
def test_adding_a_post_never_steals_a_member_from_another_group(
    session_client, workspace, project, create_bot_user
):
    """两条规则里最要紧的一条（buzz 同名分支亦然）：改组名册**绝不**抢别组的绑定。
    抢走就是把那条组的正文从这个成员的下一次运行里抹掉 —— 而且界面上什么都看不出来。
    """
    a = _definition(workspace, "需求分析")
    first = _group(workspace, name="第一组")
    second = _group(workspace, name="第二组")
    member = _member(workspace, project, create_bot_user, a, group=first)

    response = session_client.patch(
        f"{_groups_url(workspace)}{second.id}/", {"definition_ids": [str(a.id)]}, format="json"
    )

    assert response.status_code == 200
    member.refresh_from_db()
    assert member.group_id == first.id


@pytest.mark.django_db
def test_removing_a_post_unbinds_only_this_groups_members(
    session_client, workspace, project, create_user, create_bot_user
):
    """同一个岗位可以同时在两组的名册里；从一组的名单里摘掉它，**只管**绑在这一组的人。"""
    a = _definition(workspace, "需求分析")
    first = _group(workspace, name="第一组")
    second = _group(workspace, name="第二组")
    first.definitions.add(a)
    second.definitions.add(a)
    mine = _member(workspace, project, create_bot_user, a, group=first)
    theirs = _member(workspace, _other_project(workspace, create_user), create_bot_user, a, group=second)

    response = session_client.patch(
        f"{_groups_url(workspace)}{first.id}/", {"definition_ids": []}, format="json"
    )

    assert response.status_code == 200
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.group_id is None
    assert theirs.group_id == second.id


@pytest.mark.django_db
def test_a_roster_edit_can_clear_a_since_deleted_post(
    session_client, workspace, project, create_user, no_worker
):
    """名册里留着一个**已经删掉的**岗位 ⇒ 这个组永远部署不了（体检 409）。
    修法是「改组、把它摘出来」，而那一次 PATCH 送的是**活着的**名册（表单只列活岗位）。

    ⇒ 差集必须用 ``raw_roster_ids``（全部 through 行，含软删）算。用反向 M2M 读出的
    活集合算，会得出「没有变化」⇒ 这次编辑**静默空转** ⇒ 组永远卡在 409，而界面上
    没有任何东西能修它（活岗位列表里根本没有那个死岗位可摘）。
    """
    a = _definition(workspace, "需求分析")
    b = _definition(workspace, "架构设计")
    group = _group(workspace)
    group.definitions.add(a, b)

    AgentDefinition.objects.filter(pk=b.pk).delete()  # queryset 形式：不发级联任务

    deploy_url = _deploy_url(workspace, project, group)
    blocked = session_client.post(deploy_url, {}, format="json")
    assert blocked.status_code == 409
    assert "架构设计" in blocked.json()["error"]

    fixed = session_client.patch(
        f"{_groups_url(workspace)}{group.id}/", {"definition_ids": [str(a.id)]}, format="json"
    )
    assert fixed.status_code == 200

    assert session_client.post(deploy_url, {}, format="json").status_code == 200
    assert AgentMember.objects.filter(
        definition=a, project=project, deleted_at__isnull=True
    ).exists()


# ---------------------------------------------------------------------------
# 部署（设计 §3）
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_deploy_fans_out_over_the_roster_and_reports_the_cells(
    session_client, workspace, project, create_bot_user, no_worker
):
    """四格里一次跑满三格：新建两个、绑定一个（已有成员但没绑组）。"""
    fresh_a = _definition(workspace, "需求分析")
    fresh_b = _definition(workspace, "架构设计")
    preexisting = _definition(workspace, "任务拆解")
    member = _member(workspace, project, create_bot_user, preexisting)  # group=None

    group = _group(workspace)
    group.definitions.add(fresh_a, fresh_b, preexisting)

    response = session_client.post(_deploy_url(workspace, project, group), {}, format="json")

    assert response.status_code == 200
    assert sorted(row["name"] for row in response.data["created"]) == ["架构设计", "需求分析"]
    assert [row["name"] for row in response.data["bound"]] == ["任务拆解"]
    assert response.data["conflicts"] == []
    assert response.data["unchanged"] == []
    assert response.data["group"] == {"id": str(group.id), "name": "三人小组"}

    member.refresh_from_db()
    assert member.group_id == group.id  # 绑定，而不是重建
    created = AgentMember.objects.get(definition=fresh_a, project=project, deleted_at__isnull=True)
    assert created.group_id == group.id
    assert created.service_token_id is not None  # 真的铸了身份，不只是写了一行


@pytest.mark.django_db
def test_deploying_twice_reports_everything_unchanged(
    session_client, workspace, project, create_bot_user, no_worker
):
    """幂等：第二次部署不该再建成员（``(project, definition)`` 唯一约束），也不该报冲突。"""
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)
    url = _deploy_url(workspace, project, group)

    assert session_client.post(url, {}, format="json").status_code == 200
    second = session_client.post(url, {}, format="json")

    assert second.status_code == 200
    assert [row["name"] for row in second.data["unchanged"]] == ["需求分析"]
    assert second.data["created"] == [] and second.data["bound"] == []
    assert AgentMember.objects.filter(definition=a, project=project, deleted_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_deploy_never_steals_a_member_bound_to_another_group(
    session_client, workspace, project, create_bot_user, no_worker
):
    """罗盘没有 buzz 的 ``forceNewInstance``（一个岗位属两组就铸两个实例），因为
    ``(project, definition)`` 的唯一约束在仲裁 ⇒ 第二条组的答案是**拒绝并报告**，
    而不是把已部署成员静静地改挂到别的组 —— 那会改掉它跑起来时的正文。
    """
    a = _definition(workspace, "需求分析")
    first = _group(workspace, name="第一组")
    second = _group(workspace, name="第二组")
    second.definitions.add(a)
    member = _member(workspace, project, create_bot_user, a, group=first)

    response = session_client.post(_deploy_url(workspace, project, second), {}, format="json")

    assert response.status_code == 200
    assert [row["name"] for row in response.data["conflicts"]] == ["需求分析"]
    assert response.data["created"] == [] and response.data["bound"] == []
    member.refresh_from_db()
    assert member.group_id == first.id


@pytest.mark.django_db
def test_a_group_from_another_workspace_cannot_be_deployed(
    session_client, workspace, project, create_user, no_worker
):
    """部署视图必须按 ``workspace__slug`` 收窄组 —— 否则 A 工作区的项目管理员能部署
    B 工作区的组，而 ``_deploy`` 照样铸身份（它的 ``workspace`` 是现从 ``project``
    上取的，不看组属于谁）。
    """
    from plane.db.models import Workspace

    other = Workspace.objects.create(name="别的工作区", slug="other-ws", owner=create_user)
    foreign = AgentDefinition.objects.create(workspace_id=other.id, name="外面的岗位")
    foreign_group = AgentGroup.objects.create(workspace_id=other.id, name="外面的组")
    foreign_group.definitions.add(foreign)

    response = session_client.post(_deploy_url(workspace, project, foreign_group), {}, format="json")

    assert response.status_code == 404
    assert not AgentMember.objects.filter(definition=foreign, project=project).exists()


@pytest.mark.django_db
def test_an_empty_group_deploys_to_nothing(session_client, workspace, project, no_worker):
    """空名册部署是空转，不是错误 —— 四格全空、200。"""
    group = _group(workspace)
    response = session_client.post(_deploy_url(workspace, project, group), {}, format="json")
    assert response.status_code == 200
    assert all(response.data[key] == [] for key in ("created", "bound", "unchanged", "conflicts"))


# ---------------------------------------------------------------------------
# 删组（设计 §5，拒绝式）
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_group_with_bound_members_cannot_be_deleted(
    session_client, workspace, project, create_bot_user
):
    """还有成员绑着就拒绝删，并报出项目名 —— buzz 的 ``delete_team_with_cascade`` 同款
    （「已部署的 agent 不受影响」是**拒绝的结果**，不是删除的行为）。
    """
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)
    _member(workspace, project, create_bot_user, a, group=group)

    response = session_client.delete(f"{_groups_url(workspace)}{group.id}/")

    assert response.status_code == 409
    assert project.name in response.json()["error"]
    assert AgentGroup.objects.filter(pk=group.id, deleted_at__isnull=True).exists()


@pytest.mark.django_db
def test_the_delete_guard_counts_projects_not_members(
    session_client, workspace, project, create_bot_user
):
    """终审 C5：同一个组在同一个项目里可以有好几个成员（``(project, definition)`` 唯一，
    **组不唯一**）⇒ 数字和名单都必须按 ``project_id`` 去重，照抄岗位那边的写法会输出
    「仍被 2 个项目使用：P、P」。
    """
    a = _definition(workspace, "需求分析")
    b = _definition(workspace, "架构设计")
    group = _group(workspace)
    group.definitions.add(a, b)
    _member(workspace, project, create_bot_user, a, group=group)
    _member(workspace, project, create_bot_user, b, group=group)

    response = session_client.delete(f"{_groups_url(workspace)}{group.id}/")

    assert response.status_code == 409
    message = response.json()["error"]
    assert "1 个项目" in message
    assert message.count(project.name) == 1


@pytest.mark.django_db
def test_an_empty_group_can_be_deleted(session_client, workspace):
    group = _group(workspace)

    response = session_client.delete(f"{_groups_url(workspace)}{group.id}/")

    assert response.status_code == 204
    assert not AgentGroup.objects.filter(pk=group.id, deleted_at__isnull=True).exists()


@pytest.mark.django_db
def test_a_soft_deleted_member_does_not_block_deleting_the_group(
    session_client, workspace, project, create_bot_user
):
    """守卫数的是**活着的**成员 —— 从项目里移除过的成员行还在库里，但不该挡住删组。"""
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)
    member = _member(workspace, project, create_bot_user, a, group=group)
    AgentMember.objects.filter(pk=member.pk).delete()

    response = session_client.delete(f"{_groups_url(workspace)}{group.id}/")

    assert response.status_code == 204


# ---------------------------------------------------------------------------
# 岗位的合并守卫（T7）：在名册里就拒删
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_post_in_a_groups_roster_cannot_be_deleted(session_client, workspace):
    """buzz 的 ``deletePersona`` 同样拒绝 team-referenced 的 persona。
    修法明说在错误里：「先从那些组里摘出来」。
    """
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)

    response = session_client.delete(
        f"/api/workspaces/{workspace.slug}/agent-definitions/{a.id}/"
    )

    assert response.status_code == 409
    assert group.name in response.json()["error"]
    assert AgentDefinition.objects.filter(pk=a.id, deleted_at__isnull=True).exists()


@pytest.mark.django_db
def test_a_post_only_in_a_deleted_groups_roster_can_be_deleted(session_client, workspace):
    """``definition.groups`` 是**反向 M2M ⇒ 过滤软删的组**，所以「组早就删了、through 行
    还挂着」不该挡住岗位的删除。这正是那条守卫想要的行为（钉死在
    ``tests/unit/models/test_agent_group.py``）。
    """
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)
    AgentGroup.objects.filter(pk=group.pk).delete()

    response = session_client.delete(
        f"/api/workspaces/{workspace.slug}/agent-definitions/{a.id}/"
    )

    assert response.status_code == 204


# ---------------------------------------------------------------------------
# 成员行要显示得出所属组（T8 的界面依赖它）
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_member_row_reports_its_group(
    session_client, workspace, project, create_bot_user, no_worker
):
    a = _definition(workspace, "需求分析")
    group = _group(workspace)
    group.definitions.add(a)
    session_client.post(_deploy_url(workspace, project, group), {}, format="json")

    response = session_client.get(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/"
    )

    assert response.status_code == 200
    assert response.data[0]["group"] == {"id": str(group.id), "name": "三人小组"}


@pytest.mark.django_db
def test_a_member_without_a_group_reports_null(
    session_client, workspace, project, create_bot_user
):
    _member(workspace, project, create_bot_user, _definition(workspace, "需求分析"))

    response = session_client.get(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/"
    )

    assert response.status_code == 200
    assert response.data[0]["group"] is None
