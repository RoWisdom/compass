# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import AgentDefinition, AgentMember, AgentRunStatusEnum, IssueComment
from plane.utils.agent_callback import (
    post_bot_comment,
    set_issue_state_group,
    state_group_for_run_outcome,
)


@pytest.mark.django_db
def test_post_bot_comment_authors_it_as_the_bot(create_bot_user, workspace, project, create_issue):
    definition = AgentDefinition.objects.create(workspace_id=workspace.id, name="需求分析师")
    member = AgentMember.objects.create(
        definition=definition,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
    )
    comment = post_bot_comment(member=member, issue=create_issue, html="<p>我看完了。</p>")

    assert comment.actor_id == create_bot_user.id
    assert comment.created_by_id == create_bot_user.id
    assert comment.comment_stripped == "我看完了。"
    assert IssueComment.objects.filter(issue_id=create_issue.id).count() == 1


@pytest.mark.django_db
def test_set_issue_state_group_moves_only_to_an_existing_state(create_issue):
    from plane.db.models import State, StateGroup

    state = State.objects.create(
        name="进行中",
        group=StateGroup.STARTED.value,
        project_id=create_issue.project_id,
        workspace_id=create_issue.workspace_id,
        color="#fff",
    )
    assert set_issue_state_group(create_issue, StateGroup.STARTED.value) is True
    create_issue.refresh_from_db()
    assert create_issue.state_id == state.id


def test_state_group_for_run_outcome_only_ledger_moves():
    assert (
        state_group_for_run_outcome(
            AgentRunStatusEnum.SUCCEEDED.value, tier="ledger"
        )
        == "started"
    )
    # 失败也不搬 —— 设计 §5「失败也回程」：回一条评论，卡片留在原地让人重试
    assert state_group_for_run_outcome(AgentRunStatusEnum.FAILED.value, tier="ledger") is None
    # 甲/乙 不动台账 —— 设计 §6 剧本第 7 步「卡片状态没变」
    assert state_group_for_run_outcome(AgentRunStatusEnum.SUCCEEDED.value, tier="readonly") is None
    assert state_group_for_run_outcome(AgentRunStatusEnum.SUCCEEDED.value, tier="writer") is None
    # 等待批准不是一个「结果」
    assert state_group_for_run_outcome(AgentRunStatusEnum.AWAITING_APPROVAL.value, tier="ledger") is None
