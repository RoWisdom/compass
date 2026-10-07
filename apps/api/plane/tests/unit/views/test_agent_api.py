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
def test_list_members_only_returns_this_projects_active_ones(
    session_client, create_user, create_bot_user, workspace, project, no_worker
):
    _member(project, workspace, create_bot_user, name="需求分析")
    _member(project, workspace, create_bot_user, name="停用的", active=False)

    response = session_client.get(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-members/"
    )
    assert response.status_code == 200
    names = [row["name"] for row in response.data]
    assert names == ["需求分析"]


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
