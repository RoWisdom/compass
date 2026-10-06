# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import Issue

DETAIL = "/api/v1/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/"
LIST = "/api/v1/workspaces/{slug}/projects/{project_id}/issues/"
INTAKE_DETAIL = "/api/v1/workspaces/{slug}/projects/{project_id}/intake-issues/{issue_id}/"


@pytest.mark.django_db
def test_bot_cannot_set_state_on_patch(
    bot_api_key_client, workspace, project, create_issue, create_state
):
    """丙档要能改别的字段 —— 只是不能改 state。"""
    client = bot_api_key_client
    url = DETAIL.format(slug=workspace.slug, project_id=project.id, issue_id=create_issue.id)

    # Capture the starting state so the assertion below tests "unchanged" rather
    # than "None": `Issue.save()` claims a default state when none is set, so
    # whether `state_id` starts as None depends on fixture ordering.
    before = create_issue.state_id

    response = client.patch(url, {"state": str(create_state.id)}, format="json")
    assert response.status_code == 400
    assert "state" in str(response.data)

    create_issue.refresh_from_db()
    assert create_issue.state_id == before

    # 同一个 bot 改个标题是允许的 —— 守卫不是「把 bot 关掉」
    ok = client.patch(url, {"name": "改个名字"}, format="json")
    assert ok.status_code == 200
    create_issue.refresh_from_db()
    assert create_issue.name == "改个名字"


@pytest.mark.django_db
def test_bot_cannot_set_state_on_create(
    bot_api_key_client, workspace, project, create_state
):
    client = bot_api_key_client
    url = LIST.format(slug=workspace.slug, project_id=project.id)

    response = client.post(url, {"name": "新卡", "state": str(create_state.id)}, format="json")
    assert response.status_code == 400
    assert Issue.objects.filter(name="新卡").count() == 0


@pytest.mark.django_db
def test_human_still_sets_state(
    api_key_client, workspace, project, create_issue, create_state
):
    """守卫只认 is_bot —— 人走同一条路不受影响。"""
    client = api_key_client
    url = DETAIL.format(slug=workspace.slug, project_id=project.id, issue_id=create_issue.id)

    response = client.patch(url, {"state": str(create_state.id)}, format="json")
    assert response.status_code == 200
    create_issue.refresh_from_db()
    assert create_issue.state_id == create_state.id


@pytest.mark.django_db
def test_bot_cannot_set_state_via_intake(
    bot_api_key_client, create_bot_user, workspace, project, create_user, create_state
):
    """守卫也管 intake 路 —— bot 不能借 `issue.state` 挪卡片。

    The intake route nests the work-item fields under ``issue``, so a check
    against top-level ``request.data`` would look straight past them.
    """
    from plane.db.models import Intake, IntakeIssue, ProjectMember, State, StateGroup

    # The guard exists to constrain one specific tier. A project member with
    # ``role <= 5`` is turned away earlier ("You cannot edit intake work items"),
    # and ``role <= 5`` is also the only tier the name/description narrowing
    # rewrites — so only a role-15 member's nested payload reaches the serializer
    # raw. Pin that precondition rather than assuming the fixture grants it.
    member = ProjectMember.objects.get(project=project, member=create_bot_user, is_active=True)
    assert member.role == 15

    # Rows are built directly rather than through the intake create endpoint: that
    # endpoint is itself an unguarded write path, so routing setup through it would
    # make this test depend on the very behaviour it is pinning. A direct start
    # also fixes a known, non-null state to assert "unchanged" against.
    intake = Intake.objects.create(name="Test Intake", project=project, created_by=create_user)
    before_state = State.objects.create(
        name="Todo",
        group=StateGroup.UNSTARTED.value,
        color="#000000",
        project=project,
        workspace=workspace,
        created_by=create_user,
    )
    issue = Issue.objects.create(
        name="Intake Work Item",
        project=project,
        workspace=workspace,
        state=before_state,
        created_by=create_user,
    )
    IntakeIssue.objects.create(
        intake=intake,
        issue=issue,
        project=project,
        workspace=workspace,
        created_by=create_user,
    )

    url = INTAKE_DETAIL.format(slug=workspace.slug, project_id=project.id, issue_id=issue.id)
    response = bot_api_key_client.patch(url, {"issue": {"state": str(create_state.id)}}, format="json")

    # The guard runs before the serializer, so the rejection must be *its* response
    # and not a downstream validation error. Assert the body, not only the status:
    # the serializer here is built without context, so it independently rejects the
    # same payload ("State is not valid…") and a status-only assertion would not
    # tell the two apart — this body is what goes red when the guard is removed.
    assert response.status_code == 400
    assert response.data.get("error") == (
        "An AI member cannot change the work item state; Plane's own code owns that transition."
    )
    assert response.data.get("fields") == ["state"]

    issue.refresh_from_db()
    assert issue.state_id == before_state.id
