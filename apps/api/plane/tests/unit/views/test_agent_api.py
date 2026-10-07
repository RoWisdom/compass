# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import AgentMember, AgentRun, AgentRunStatusEnum, AgentTierEnum


@pytest.fixture
def no_worker(monkeypatch):
    monkeypatch.setattr("plane.app.views.agent.run_agent_member.delay", lambda run_id: None)


def _member(project, workspace, bot_user, name="需求分析", tier=AgentTierEnum.READONLY.value, active=True):
    return AgentMember.objects.create(
        name=name, tier=tier, is_active=active,
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
