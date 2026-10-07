# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import AgentMember, AgentRun, AgentRunStatusEnum, AgentTierEnum, IssueComment
from plane.db.signals.agent_approval import APPROVAL_PREFIX, _claim_for_rerun

# Every save below is wrapped in ``django_capture_on_commit_callbacks(execute=True)``.
# The signal enqueues through ``transaction.on_commit``, which does not fire inside the
# test's atomic block unless it is asked to. Wrapping **all three** tests matters as much
# for the negative ones as for the positive one: unwrapped, "no callback ran" and "the
# callback decided not to enqueue" look identical, and the two anti-loop tests would pass
# no matter what the signal did.


def _member(project, workspace, bot_user, tier=AgentTierEnum.LEDGER.value):
    return AgentMember.objects.create(
        name="任务拆解", tier=tier,
        project_id=project.id, workspace_id=workspace.id, bot_user_id=bot_user.id,
    )


def _awaiting(member, issue, project, workspace):
    return AgentRun.objects.create(
        member=member, issue_id=issue.id, project_id=project.id, workspace_id=workspace.id,
        status=AgentRunStatusEnum.AWAITING_APPROVAL.value, plan="建两张子卡。",
    )


@pytest.mark.django_db
def test_a_human_replying_approval_requeues_the_run(
    create_bot_user, create_user, workspace, project, create_issue,
    monkeypatch, django_capture_on_commit_callbacks,
):
    enqueued = []
    monkeypatch.setattr(
        "plane.db.signals.agent_approval.run_agent_member.delay",
        lambda run_id: enqueued.append(run_id),
    )
    member = _member(project, workspace, create_bot_user)
    run = _awaiting(member, create_issue, project, workspace)

    comment = IssueComment(
        project_id=project.id, workspace_id=workspace.id, issue_id=create_issue.id,
        actor_id=create_user.id, comment_html=f"<p>{APPROVAL_PREFIX}，动手吧。</p>",
    )
    with django_capture_on_commit_callbacks(execute=True):
        comment.save(created_by_id=create_user.id)

    run.refresh_from_db()
    assert run.status == AgentRunStatusEnum.PENDING.value
    assert enqueued == [str(run.id)]


@pytest.mark.django_db
def test_a_bot_comment_never_requeues_anything(
    create_bot_user, workspace, project, create_issue,
    monkeypatch, django_capture_on_commit_callbacks,
):
    """防环 —— 设计 §5 就这一条规则。"""
    enqueued = []
    monkeypatch.setattr(
        "plane.db.signals.agent_approval.run_agent_member.delay",
        lambda run_id: enqueued.append(run_id),
    )
    member = _member(project, workspace, create_bot_user)
    run = _awaiting(member, create_issue, project, workspace)

    # 一条「计划评论」：内容也以「批准」开头，作者是 bot —— 必须一动不动
    comment = IssueComment(
        project_id=project.id, workspace_id=workspace.id, issue_id=create_issue.id,
        actor_id=create_bot_user.id, comment_html=f"<p>{APPROVAL_PREFIX}前请先看这段话。</p>",
    )
    with django_capture_on_commit_callbacks(execute=True):
        comment.save(created_by_id=create_bot_user.id)

    run.refresh_from_db()
    assert run.status == AgentRunStatusEnum.AWAITING_APPROVAL.value
    assert enqueued == []


@pytest.mark.django_db
def test_an_ordinary_human_comment_changes_nothing(
    create_user, workspace, project, create_issue, create_bot_user,
    monkeypatch, django_capture_on_commit_callbacks,
):
    enqueued = []
    monkeypatch.setattr(
        "plane.db.signals.agent_approval.run_agent_member.delay",
        lambda run_id: enqueued.append(run_id),
    )
    member = _member(project, workspace, create_bot_user)
    run = _awaiting(member, create_issue, project, workspace)

    comment = IssueComment(
        project_id=project.id, workspace_id=workspace.id, issue_id=create_issue.id,
        actor_id=create_user.id, comment_html="<p>这个方案我看看。</p>",
    )
    with django_capture_on_commit_callbacks(execute=True):
        comment.save(created_by_id=create_user.id)

    run.refresh_from_db()
    assert run.status == AgentRunStatusEnum.AWAITING_APPROVAL.value
    assert enqueued == []


@pytest.mark.django_db
def test_only_one_of_two_overlapping_approvals_claims_the_run(
    create_bot_user, workspace, project, create_issue,
):
    """M-26：两次重叠的「批准」只有一个能把状态翻走 —— 认领是条件式的。

    第二个批准者手里那份 run 是**旧快照**（它读到的是 awaiting_approval），但它的写入是
    条件式的，所以只拿到 0 行、不入队。这条测试会红，如果谁把它改回读-改-写。
    """
    member = _member(project, workspace, create_bot_user)
    run = _awaiting(member, create_issue, project, workspace)

    assert _claim_for_rerun(run) is True
    assert _claim_for_rerun(run) is False
    run.refresh_from_db()
    assert run.status == AgentRunStatusEnum.PENDING.value
