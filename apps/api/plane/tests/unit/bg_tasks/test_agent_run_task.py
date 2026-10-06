# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from pathlib import Path

import pytest

from plane.db.models import AgentMember, AgentRun, AgentRunStatusEnum, AgentTierEnum, IssueComment
from plane.bgtasks.agent_run_task import run_agent_member


@pytest.mark.django_db
def test_successful_non_ledger_run_posts_comment_and_moves_nothing(
    create_bot_user, workspace, project, create_issue, monkeypatch
):
    member = AgentMember.objects.create(
        name="需求分析",
        tier=AgentTierEnum.READONLY.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
    )
    run = AgentRun.objects.create(
        member=member, issue_id=create_issue.id,
        project_id=project.id, workspace_id=workspace.id,
    )

    def fake_run_headless(**kwargs):
        # 造一个「岛里多了一个文件」的现场，让 artifacts 有东西可算
        island = Path(kwargs["island"])
        island.mkdir(parents=True, exist_ok=True)
        (island / "分析.md").write_text("结论", encoding="utf-8")
        return 0, "这是一条结论。", ""

    monkeypatch.setattr("plane.bgtasks.agent_run_task._run_headless", fake_run_headless)

    run_agent_member(str(run.id))
    run.refresh_from_db()

    assert run.status == AgentRunStatusEnum.SUCCEEDED.value
    assert run.exit_code == 0
    assert run.artifacts == ["分析.md"]
    assert IssueComment.objects.filter(issue_id=create_issue.id, actor_id=create_bot_user.id).count() == 1
    # 甲档不动台账 —— 设计 §6 剧本第 7 步
    create_issue.refresh_from_db()
    assert create_issue.state_id is None


@pytest.mark.django_db
def test_ledger_first_round_stops_at_awaiting_approval(
    create_bot_user, workspace, project, create_issue, monkeypatch
):
    member = AgentMember.objects.create(
        name="任务拆解",
        tier=AgentTierEnum.LEDGER.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
    )
    run = AgentRun.objects.create(
        member=member, issue_id=create_issue.id,
        project_id=project.id, workspace_id=workspace.id,
    )
    monkeypatch.setattr(
        "plane.bgtasks.agent_run_task._run_headless",
        lambda **kwargs: (0, "计划：建两张子卡。", ""),
    )

    run_agent_member(str(run.id))
    run.refresh_from_db()

    assert run.status == AgentRunStatusEnum.AWAITING_APPROVAL.value
    assert run.plan == "计划：建两张子卡。"
    assert IssueComment.objects.filter(issue_id=create_issue.id).count() == 1
    # 还没批，卡片一个字没动（设计 §6 剧本第 3 步）
    create_issue.refresh_from_db()
    assert create_issue.state_id is None


@pytest.mark.django_db
def test_ledger_second_round_executes_the_stored_plan(
    create_bot_user, workspace, project, create_issue, create_state, monkeypatch
):
    member = AgentMember.objects.create(
        name="任务拆解",
        tier=AgentTierEnum.LEDGER.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
    )
    run = AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id,
        workspace_id=workspace.id, plan="计划：建两张子卡。",
        status=AgentRunStatusEnum.PENDING.value,
    )

    seen = {}

    def fake_run_headless(**kwargs):
        seen["prompt"] = kwargs["prompt"]
        return 0, "做完了。", ""

    monkeypatch.setattr("plane.bgtasks.agent_run_task._run_headless", fake_run_headless)

    run_agent_member(str(run.id))
    run.refresh_from_db()

    assert "计划：建两张子卡。" in seen["prompt"]
    assert "按这份计划动手" in seen["prompt"]
    assert run.status == AgentRunStatusEnum.SUCCEEDED.value
    create_issue.refresh_from_db()
    assert create_issue.state_id == create_state.id


@pytest.mark.django_db
def test_failure_posts_a_comment_and_does_not_move_the_card(
    create_bot_user, workspace, project, create_issue, monkeypatch
):
    member = AgentMember.objects.create(
        name="任务拆解",
        tier=AgentTierEnum.LEDGER.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
    )
    run = AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id,
        workspace_id=workspace.id, plan="已经批过的计划",
    )
    monkeypatch.setattr(
        "plane.bgtasks.agent_run_task._run_headless",
        lambda **kwargs: (1, "", "dsh: headless_aborted: 模型拒答"),
    )

    run_agent_member(str(run.id))
    run.refresh_from_db()

    assert run.status == AgentRunStatusEnum.FAILED.value
    assert "headless_aborted" in run.error
    comment = IssueComment.objects.get(issue_id=create_issue.id)
    assert "headless_aborted" in comment.comment_html
    create_issue.refresh_from_db()
    assert create_issue.state_id is None


@pytest.mark.django_db
def test_task_is_idempotent_on_a_finished_run(
    create_bot_user, workspace, project, create_issue, monkeypatch
):
    member = AgentMember.objects.create(
        name="需求分析", project_id=project.id, workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
    )
    run = AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id,
        workspace_id=workspace.id, status=AgentRunStatusEnum.SUCCEEDED.value,
    )
    called = {"n": 0}
    monkeypatch.setattr(
        "plane.bgtasks.agent_run_task._run_headless",
        lambda **kwargs: called.__setitem__("n", called["n"] + 1) or (0, "", ""),
    )

    run_agent_member(str(run.id))
    assert called["n"] == 0
