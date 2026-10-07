# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from pathlib import Path

import pytest

from plane.db.models import (
    APIToken,
    AgentMember,
    AgentRun,
    AgentRunStatusEnum,
    AgentTierEnum,
    IssueComment,
)
from plane.bgtasks.agent_run_task import run_agent_member
from plane.utils.markdown_storage import project_directory


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
    create_bot_user, create_user, workspace, project, create_issue, monkeypatch
):
    """设计 §5「失败也回程」：失败要说话，但**不动状态**。

    卡片先停在「进行中」（上一轮成功搬过去的），项目里也真的建了「待办」——
    所以下面那条断言有一个它**本来会被搬去**的地方。没有这两下，这个测试是空转的。
    """
    from plane.db.models import Issue, State, StateGroup

    started = State.objects.create(
        name="进行中", group=StateGroup.STARTED.value, color="#F59E0B",
        project_id=project.id, workspace_id=workspace.id, created_by=create_user,
    )
    State.objects.create(
        name="待办", group=StateGroup.UNSTARTED.value, color="#E5E5E5",
        project_id=project.id, workspace_id=workspace.id, created_by=create_user,
    )
    # 用 ``.update()`` 而不是 ``save()``：绕开 ``Issue.save`` 的默认状态逻辑，
    # 让这条测试的起点毫不含糊就是「进行中」。
    Issue.objects.filter(pk=create_issue.pk).update(state_id=started.id)
    create_issue.refresh_from_db()
    assert create_issue.state_id == started.id

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
    assert create_issue.state_id == started.id
    # 「待办」真的在 —— 上面那条断言本来可以失败（防止它再变回空转）
    assert State.objects.filter(project_id=project.id, group=StateGroup.UNSTARTED.value).exists()


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


@pytest.mark.django_db
def test_a_missing_dsh_binary_still_posts_a_comment(
    create_bot_user, workspace, project, create_issue, monkeypatch
):
    """Step 7 的那个部署事故：PATH 里没有 `dsh`。它也得在卡片上说话。"""
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

    def raise_missing(**kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'dsh'")

    monkeypatch.setattr("plane.bgtasks.agent_run_task._run_headless", raise_missing)

    run_agent_member(str(run.id))
    run.refresh_from_db()

    assert run.status == AgentRunStatusEnum.FAILED.value
    assert run.exit_code == 127
    comment = IssueComment.objects.get(issue_id=create_issue.id)
    assert "Could not start" in comment.comment_html


@pytest.mark.django_db
def test_a_crash_inside_execute_still_posts_a_comment(
    create_bot_user, workspace, project, create_issue, monkeypatch
):
    """`_execute` 自己炸了（不是子进程炸了）也要说话 —— 兜底那条路的判据。"""
    member = AgentMember.objects.create(
        name="任务拆解",
        tier=AgentTierEnum.LEDGER.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
    )
    run = AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id,
        workspace_id=workspace.id,
    )

    def boom(_run):
        raise RuntimeError("boom")

    monkeypatch.setattr("plane.bgtasks.agent_run_task._execute", boom)

    run_agent_member(str(run.id))
    run.refresh_from_db()

    assert run.status == AgentRunStatusEnum.FAILED.value
    assert run.error == "boom"
    comment = IssueComment.objects.get(issue_id=create_issue.id)
    assert "boom" in comment.comment_html


@pytest.mark.django_db
def test_a_leaked_token_is_redacted_in_the_comment_and_the_log(
    create_bot_user, workspace, project, create_issue, monkeypatch
):
    """设计 §2「token 不落盘」：子进程回显了 env，那两个落点都必须是干净的。"""
    secret = "plane_svc_tok_THIS_IS_NOT_A_REAL_VALUE"
    # ``AgentMember.service_token`` 是 FK（反向名 ``agent_members``），不是 property ——
    # 所以按真实形状来：建一行真 token 挂上去，让 ``_execute`` 读到我们已知的那个值。
    service_token = APIToken.objects.create(
        label="agent service token", user=create_bot_user, user_type=1, token=secret
    )
    member = AgentMember.objects.create(
        name="任务拆解",
        tier=AgentTierEnum.LEDGER.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
        service_token=service_token,
    )
    run = AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id,
        workspace_id=workspace.id, plan="已经批过的计划",
    )
    # 子进程把 env 打了出来，然后失败 —— 这正是最现实的泄漏姿势
    monkeypatch.setattr(
        "plane.bgtasks.agent_run_task._run_headless",
        lambda **kwargs: (1, f"PLANE_API_KEY={secret}\nboom", ""),
    )

    run_agent_member(str(run.id))
    run.refresh_from_db()

    assert run.status == AgentRunStatusEnum.FAILED.value
    assert secret not in (run.error or "")
    comment = IssueComment.objects.get(issue_id=create_issue.id)
    assert secret not in comment.comment_html
    # 日志那两个落点同样干净
    logs = list(Path(str(project_directory(workspace, project))).rglob("*.log"))
    assert logs, "这次运行本该在岛里留下一份日志"
    for log in logs:
        assert secret not in log.read_text(encoding="utf-8")


@pytest.mark.django_db
def test_a_raising_success_comment_does_not_turn_a_success_into_a_failure(
    create_bot_user, workspace, project, create_issue, create_state, monkeypatch
):
    """M-24：说话失败不能把一次成功的落地改写成失败，也不能吞掉那次搬卡。"""
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
    monkeypatch.setattr("plane.bgtasks.agent_run_task._run_headless", lambda **kwargs: (0, "做完了。", ""))

    def exploding_comment(**kwargs):
        raise RuntimeError("comment sink is down")

    monkeypatch.setattr("plane.bgtasks.agent_run_task.post_bot_comment", exploding_comment)

    run_agent_member(str(run.id))
    run.refresh_from_db()

    assert run.status == AgentRunStatusEnum.SUCCEEDED.value
    create_issue.refresh_from_db()
    assert create_issue.state_id == create_state.id  # 搬卡仍然发生了


@pytest.mark.django_db
def test_html_in_the_output_is_escaped(
    create_bot_user, workspace, project, create_issue, monkeypatch
):
    """M-21：< 和 & 必须原样显示，而不是被 HTML 解析器吃掉。"""
    member = AgentMember.objects.create(
        name="需求分析",
        tier=AgentTierEnum.READONLY.value,
        project_id=project.id,
        workspace_id=workspace.id,
        bot_user_id=create_bot_user.id,
    )
    run = AgentRun.objects.create(
        member=member, issue_id=create_issue.id, project_id=project.id,
        workspace_id=workspace.id,
    )
    monkeypatch.setattr(
        "plane.bgtasks.agent_run_task._run_headless",
        lambda **kwargs: (0, "a < b & c <script>alert(1)</script>", ""),
    )

    run_agent_member(str(run.id))
    comment = IssueComment.objects.get(issue_id=create_issue.id)

    assert "<script>" not in comment.comment_html
    assert "&lt;script&gt;" in comment.comment_html
    assert "a &lt; b &amp; c" in comment.comment_html
