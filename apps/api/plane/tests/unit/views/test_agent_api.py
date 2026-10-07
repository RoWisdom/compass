# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from django.db import IntegrityError, transaction

from plane.db.models import (
    AGENT_UNFINISHED_STATUSES,
    AgentMember,
    AgentRun,
    AgentRunStatusEnum,
    AgentTierEnum,
)


@pytest.fixture
def no_worker(monkeypatch):
    """本测试内不允许有任何后台任务离开进程。

    两个口子：唤醒（``create``）与软删级联（``SoftDeleteModel.delete`` —— 见
    ``db/mixins.py``，它 ``soft_delete_related_objects.delay``）。测试没设
    ``CELERY_TASK_ALWAYS_EAGER``，漏堵一个就会往 226 的 broker 真发任务。
    """
    monkeypatch.setattr("plane.app.views.agent.run_agent_member.delay", lambda run_id: None)
    monkeypatch.setattr("plane.bgtasks.deletion_task.soft_delete_related_objects.delay", lambda *a, **k: None)


def _definition(workspace, name="需求分析", tier=AgentTierEnum.READONLY.value, **kwargs):
    from plane.db.models import AgentDefinition

    return AgentDefinition.objects.create(
        workspace_id=workspace.id, name=name, tier=tier, **kwargs
    )


def _member(project, workspace, bot_user, name="需求分析", tier=AgentTierEnum.READONLY.value, active=True):
    return AgentMember.objects.create(
        definition=_definition(workspace, name=name, tier=tier),
        is_active=active,
        project_id=project.id, workspace_id=workspace.id, bot_user_id=bot_user.id,
    )


@pytest.mark.django_db
def test_list_members_includes_inactive_ones(
    session_client, create_user, create_bot_user, workspace, project, no_worker
):
    """设计 §3：名册**列出停用的**成员（buzz「永不隐藏」）。

    第一期这里过滤掉 `is_active=True`，结果是停用之后界面上看不见它、
    也没有任何通路改回来。停用只关唤醒的门，不关显示的门。
    """
    _member(project, workspace, create_bot_user, name="需求分析")
    _member(project, workspace, create_bot_user, name="架构设计", active=False)

    response = session_client.get(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/"
    )
    assert response.status_code == 200
    names = sorted(row["definition"]["name"] for row in response.data)
    assert names == ["架构设计", "需求分析"]
    by_name = {row["definition"]["name"]: row["is_active"] for row in response.data}
    assert by_name["架构设计"] is False


@pytest.mark.django_db
def test_create_run_enqueues_once(
    session_client, create_user, create_bot_user, workspace, project, create_issue, no_worker, monkeypatch
):
    enqueued = []
    monkeypatch.setattr("plane.app.views.agent.run_agent_member.delay", lambda run_id: enqueued.append(run_id))
    member = _member(project, workspace, create_bot_user)

    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/",
        {"member_id": str(member.id), "issue_id": str(create_issue.id)},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["status"] == AgentRunStatusEnum.PENDING.value
    assert len(enqueued) == 1


@pytest.mark.django_db
def test_second_click_on_the_same_member_returns_the_existing_run(
    session_client, create_bot_user, workspace, project, create_issue, no_worker
):
    """设计 §3①：同一次点击重复触发就返回已有那个未结束的 Run，不叠。"""
    member = _member(project, workspace, create_bot_user)
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/"
    body = {"member_id": str(member.id), "issue_id": str(create_issue.id)}

    first = session_client.post(url, body, format="json")
    second = session_client.post(url, body, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.data["id"] == first.data["id"]
    assert AgentRun.objects.filter(issue_id=create_issue.id).count() == 1


@pytest.mark.django_db
def test_a_different_member_on_a_busy_card_is_rejected(
    session_client, create_bot_user, workspace, project, create_issue, no_worker
):
    """设计 §5 的锁：一张卡上同时只允许一个未结束的运行。"""
    first_member = _member(project, workspace, create_bot_user, name="需求分析")
    second_member = _member(project, workspace, create_bot_user, name="架构设计", tier=AgentTierEnum.WRITER.value)
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/"

    assert session_client.post(
        url, {"member_id": str(first_member.id), "issue_id": str(create_issue.id)}, format="json"
    ).status_code == 201

    busy = session_client.post(
        url, {"member_id": str(second_member.id), "issue_id": str(create_issue.id)}, format="json"
    )
    assert busy.status_code == 400
    assert AgentRun.objects.filter(issue_id=create_issue.id).count() == 1


@pytest.mark.django_db
def test_an_inactive_member_cannot_be_woken(
    session_client, create_bot_user, workspace, project, create_issue, no_worker
):
    member = _member(project, workspace, create_bot_user, active=False)
    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/",
        {"member_id": str(member.id), "issue_id": str(create_issue.id)},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_runs_does_not_leak_another_projects_runs(
    session_client, create_user, create_bot_user, workspace, project, create_issue, no_worker
):
    """``list`` must be scoped to this project.

    ``BaseViewSet.get_queryset`` is ``self.model.objects.all()`` — unscoped. If
    ``AgentRunViewSet`` did not define ``list``, the route would fall through to
    DRF's ``ModelViewSet.list`` and serialise **every** ``AgentRun`` in the
    database, ``error``, ``artifacts`` and ``session_ref`` included, to any
    authenticated member who guessed a slug and a project id. An ``AgentRun``
    always needs its own ``Issue`` (the FK is required) and its own
    ``AgentMember`` (unique per project+name), so the foreign run gets both.
    """
    from plane.db.models import Issue, Project, ProjectMember

    mine = _member(project, workspace, create_bot_user, name="需求分析")
    my_run = AgentRun.objects.create(
        workspace_id=workspace.id,
        project_id=project.id,
        member=mine,
        issue=create_issue,
        triggered_by_id=create_user.id,
        created_by_id=create_user.id,
    )

    other_project = Project.objects.create(
        name="Another Project", identifier="APJ", workspace=workspace, created_by=create_user
    )
    ProjectMember.objects.create(
        project=other_project, member=create_user, workspace=workspace, role=20
    )
    other_issue = Issue.objects.create(
        name="Other Work Item", project=other_project, workspace=workspace, created_by=create_user
    )
    other_member = _member(other_project, workspace, create_bot_user, name="架构设计")
    foreign_run = AgentRun.objects.create(
        workspace_id=workspace.id,
        project_id=other_project.id,
        member=other_member,
        issue=other_issue,
        triggered_by_id=create_user.id,
        created_by_id=create_user.id,
    )

    response = session_client.get(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/"
    )
    assert response.status_code == 200
    ids = [str(row["id"]) for row in response.data]
    assert str(my_run.id) in ids
    assert str(foreign_run.id) not in ids


@pytest.mark.django_db
def test_a_failed_enqueue_does_not_lock_the_card(
    session_client, create_bot_user, workspace, project, create_issue, monkeypatch
):
    """入队失败的那一行不能占住设计 §5 的锁。

    它若留在 `pending`（属于 AGENT_UNFINISHED_STATUSES），这张卡就再也醒不过来了：
    同一成员会拿回这个死 run，别的成员一律被拒。
    """

    def broker_is_down(run_id):
        raise RuntimeError("broker down")

    monkeypatch.setattr("plane.app.views.agent.run_agent_member.delay", broker_is_down)
    member = _member(project, workspace, create_bot_user)

    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/",
        {"member_id": str(member.id), "issue_id": str(create_issue.id)},
        format="json",
    )
    assert response.status_code == 400

    run = AgentRun.objects.get(issue_id=create_issue.id)
    assert run.status == AgentRunStatusEnum.FAILED.value

    # 锁真的释放了 —— 没有任何未结束的运行还占着这张卡
    assert not AgentRun.objects.filter(
        project_id=project.id,
        issue_id=create_issue.id,
        status__in=AGENT_UNFINISHED_STATUSES,
    ).exists()


@pytest.mark.django_db
def test_the_database_refuses_a_second_unfinished_run_on_one_issue(
    create_bot_user, workspace, project, create_issue,
):
    """M-35：§5 的锁现在有 DB 兜底 —— check-then-create 的窗口关死在索引上。"""
    member = _member(project, workspace, create_bot_user, name="任务拆解", tier=AgentTierEnum.LEDGER.value)
    AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id, workspace_id=workspace.id,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AgentRun.objects.create(
                member=member, issue_id=create_issue.id, project_id=project.id, workspace_id=workspace.id,
            )


@pytest.mark.django_db
def test_a_finished_run_does_not_block_the_next_one(
    create_bot_user, workspace, project, create_issue,
):
    """约束**必须**是带条件的：无条件唯一索引会把整张卡锁死，第二次运行永远建不出来。"""
    member = _member(project, workspace, create_bot_user, name="任务拆解", tier=AgentTierEnum.LEDGER.value)
    first = AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id, workspace_id=workspace.id,
        status=AgentRunStatusEnum.SUCCEEDED.value,
    )
    second = AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id, workspace_id=workspace.id,
    )
    assert second.pk != first.pk


@pytest.mark.django_db
def test_a_lost_race_returns_400_not_500(
    session_client, workspace, project, create_bot_user, create_issue, monkeypatch,
):
    """竞速的输家拿到与「已有成员在跑」**同一句**台词，而不是一句含糊的 500/兜底 400。"""
    member = _member(project, workspace, create_bot_user, name="任务拆解", tier=AgentTierEnum.LEDGER.value)

    def racing_create(**kwargs):
        raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(AgentRun.objects, "create", racing_create)

    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/",
        {"member_id": str(member.id), "issue_id": str(create_issue.id)},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["error"] == (
        "This work item already has a running AI member; wait for it or approve its plan."
    )


@pytest.mark.django_db
def test_add_a_post_to_a_project(session_client, workspace, project, create_user):
    """设计 §4.2：从岗位库挑一个加进项目，得到一行成员 + 一个 bot + 一个 token。"""
    from plane.db.models import AgentDefinition, APIToken, ProjectMember

    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", created_by_id=create_user.id
    )
    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/",
        {"definition_id": str(definition.id)},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["definition"]["name"] == "需求分析"

    member = AgentMember.objects.get(pk=response.data["id"])
    assert member.is_active is True
    assert member.service_token_id is not None
    assert ProjectMember.objects.filter(project=project, member_id=member.bot_user_id).exists()
    # token 值本身永远不出现在响应里
    assert "token" not in response.data
    assert APIToken.objects.filter(pk=member.service_token_id, is_service=True).exists()


@pytest.mark.django_db
def test_adding_the_same_post_twice_is_refused(session_client, workspace, project, create_user):
    from plane.db.models import AgentDefinition

    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", created_by_id=create_user.id
    )
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/"
    assert session_client.post(url, {"definition_id": str(definition.id)}, format="json").status_code == 201
    second = session_client.post(url, {"definition_id": str(definition.id)}, format="json")
    assert second.status_code == 409
    assert AgentMember.objects.filter(definition=definition, deleted_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_removing_a_member_retires_its_project_identity(
    session_client, workspace, project, create_user, create_bot_user, db
):
    """设计 §6②：移除**不止**是软删一行 —— 项目身份要摘、token 要废。

    留下一个还能动的 token，就是按完「移除」还留着一扇后门。
    """
    from plane.db.models import AgentDefinition, APIToken, ProjectMember

    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", created_by_id=create_user.id
    )
    created = session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/",
        {"definition_id": str(definition.id)},
        format="json",
    )
    member = AgentMember.objects.get(pk=created.data["id"])
    token_id = member.service_token_id

    response = session_client.delete(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/{member.id}/"
    )
    assert response.status_code == 204

    assert not AgentMember.objects.filter(pk=member.id, deleted_at__isnull=True).exists()
    assert not ProjectMember.objects.filter(project=project, member_id=member.bot_user_id).exists()
    token = APIToken.objects.get(pk=token_id)
    assert token.is_active is False
    # 岗位本身还在 —— 移除的是这一行，不是那个岗位
    assert AgentDefinition.objects.filter(pk=definition.id).exists()


@pytest.mark.django_db
def test_a_post_still_in_use_cannot_be_deleted(session_client, workspace, project, create_user):
    """设计 §6③：被引用就拒绝，并报出项目名。"""
    from plane.db.models import AgentDefinition

    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", created_by_id=create_user.id
    )
    session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/",
        {"definition_id": str(definition.id)}, format="json",
    )

    response = session_client.delete(f"/api/workspaces/{workspace.slug}/agent-definitions/{definition.id}/")
    assert response.status_code == 409
    assert project.name in response.json()["error"]
    assert AgentDefinition.objects.filter(pk=definition.id).exists()


@pytest.mark.django_db
def test_a_post_with_no_membership_can_be_deleted(session_client, workspace, create_user):
    from plane.db.models import AgentDefinition

    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="没人用的", created_by_id=create_user.id
    )
    response = session_client.delete(f"/api/workspaces/{workspace.slug}/agent-definitions/{definition.id}/")
    assert response.status_code == 204
    assert not AgentDefinition.objects.filter(pk=definition.id, deleted_at__isnull=True).exists()


@pytest.mark.django_db
def test_editing_a_post_is_visible_from_every_project_that_uses_it(
    session_client, workspace, project, create_user
):
    """本期的核心判据（设计 §1 推论）：一份说明书，N 个项目一起变。"""
    from plane.db.models import AgentDefinition

    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", instructions="旧", created_by_id=create_user.id
    )
    session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/",
        {"definition_id": str(definition.id)}, format="json",
    )

    patched = session_client.patch(
        f"/api/workspaces/{workspace.slug}/agent-definitions/{definition.id}/",
        {"instructions": "新"}, format="json",
    )
    assert patched.status_code == 200

    listed = session_client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/")
    row = listed.data[0]
    # 成员行里没有 instructions 字段（它属于岗位），所以判据走岗位端点
    detail = session_client.get(f"/api/workspaces/{workspace.slug}/agent-definitions/{definition.id}/")
    assert detail.data["instructions"] == "新"
    assert row["definition"]["name"] == "需求分析"


@pytest.mark.django_db
def test_a_member_cannot_be_repointed_to_another_post(
    session_client, workspace, project, create_user
):
    """设计 §2：成员行的岗位**不可改**，要换只能移除后重加。

    不拦的话（序列化器里 ``definition_id`` 是可写字段，而 DRF 的 ``update()``
    会对每个 ``validated_data`` 键 ``setattr``）一次 PATCH 就能把它悄悄改挂到
    别的岗位，却留着旧的 bot 用户与 token ⇒ 从此按新岗位的说明书、用旧岗位的凭证跑。
    """
    from plane.db.models import AgentDefinition

    first = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", created_by_id=create_user.id
    )
    second = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="架构设计", created_by_id=create_user.id
    )
    created = session_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/",
        {"definition_id": str(first.id)},
        format="json",
    )
    member_id = created.data["id"]
    bot_user_id = AgentMember.objects.get(pk=member_id).bot_user_id

    response = session_client.patch(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/{member_id}/",
        {"definition_id": str(second.id)},
        format="json",
    )
    assert response.status_code == 400
    member = AgentMember.objects.get(pk=member_id)
    assert member.definition_id == first.id
    assert member.bot_user_id == bot_user_id


@pytest.mark.django_db
def test_two_posts_cannot_share_a_name_in_one_workspace(session_client, workspace, create_user):
    """0129 的唯一约束：同一工作区里岗位名唯一。

    这条断言的是 **409 而不是 500** —— 序列化器的 `UniqueTogetherValidator` 因
    `workspace_id` 只读、无默认值而被 DRF 跳过，重名会一路撞到 INSERT。
    """
    from plane.db.models import AgentDefinition

    url = f"/api/workspaces/{workspace.slug}/agent-definitions/"
    assert session_client.post(url, {"name": "需求分析"}, format="json").status_code == 201
    second = session_client.post(url, {"name": "需求分析"}, format="json")
    assert second.status_code == 409
    assert "需求分析" in second.json()["error"]
    # 这句同时钉住 create 里那层 atomic（保存点）：去掉它，被 IntegrityError 染脏的
    # 连接会让这次查询抛 TransactionManagementError，而不是 1。
    assert AgentDefinition.objects.filter(workspace_id=workspace.id, deleted_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_renaming_a_post_onto_an_existing_name_is_refused(
    session_client, workspace, create_user
):
    """改名走的是 `partial_update`，同一条唯一约束，同样不许 500。"""
    from plane.db.models import AgentDefinition

    url = f"/api/workspaces/{workspace.slug}/agent-definitions/"
    session_client.post(url, {"name": "需求分析"}, format="json")
    other = session_client.post(url, {"name": "架构设计"}, format="json")
    response = session_client.patch(f"{url}{other.data['id']}/", {"name": "需求分析"}, format="json")
    assert response.status_code == 409
    # 改名失败后原名不动
    assert AgentDefinition.objects.get(pk=other.data["id"]).name == "架构设计"
