# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import Issue

DETAIL = "/api/v1/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/"
LIST = "/api/v1/workspaces/{slug}/projects/{project_id}/issues/"


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
