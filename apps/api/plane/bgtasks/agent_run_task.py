# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Run one AI member against one card, by starting one `dsh headless` subprocess.

The subprocess is the whole of the coupling to DSH: no plugin API, no session
file parsing, no internal imports (design §1 boundary 1). Everything we know
afterwards comes from the process's exit code, its stdout, and two directory
diffs taken around it.
"""

# Python imports
import logging
import os
import subprocess

# Django imports
from django.conf import settings
from django.utils import timezone

# Third-party imports
from celery import shared_task

# Module imports
from plane.db.models import AgentRun, AgentRunStatusEnum, AgentTierEnum
from plane.utils.agent_callback import post_bot_comment, set_issue_state_group, state_group_for_run_outcome
from plane.utils.agent_prompt import build_execute_prompt, build_plan_prompt
from plane.utils.agent_run import (
    diff_snapshot,
    new_session_ref,
    permission_mode_for_tier,
    session_dirs,
    snapshot_tree,
)
from plane.utils.exception_logger import log_exception
from plane.utils.markdown_storage import project_directory

logger = logging.getLogger(__name__)


def _failure_html(run) -> str:
    body = (run.error or "").strip() or "（没有错误输出）"
    return (
        "<p><strong>这次运行失败了，卡片没动。</strong></p>"
        f"<p>退出码：{run.exit_code if run.exit_code is not None else '—'}</p>"
        f"<pre>{body}</pre>"
        "<p>可以修好环境后重新唤醒。</p>"
    )


def _post_failure_comment(run) -> None:
    """失败就得说话 —— 静默失败是最坏的形态（设计 §5「失败也回程」）。

    尽力而为：如果连评论都发不出去，那已经没什么可做的了，记一笔就走，绝不能
    让发评论这件事本身把任务炸掉。
    """
    try:
        post_bot_comment(member=run.member, issue=run.issue, html=_failure_html(run))
    except Exception as e:
        log_exception(e)


def _success_html(stdout: str, artifacts) -> str:
    """回程第一条：结论贴成评论 + 产物路径清单（设计 §3「卡片评论里给路径清单」）。"""
    if artifacts:
        listing = "<p>岛里新增或改动的文件：</p><ul>" + "".join(
            f"<li>{name}</li>" for name in artifacts
        ) + "</ul>"
    else:
        listing = "<p>这次没有新增或改动的文件。</p>"
    return f"<p><strong>结论</strong></p><pre>{stdout}</pre>{listing}"


def _run_headless(*, prompt, island, member, env_extra):
    """Start one headless run. Returns ``(exit_code, stdout, stderr)``.

    Split out so the tests can replace it without a subprocess (and without a
    `dsh` on the test machine's PATH).

    ``cwd`` is the island and *is* the permission boundary — verified, see design
    §6.1. The token only ever exists in this child's environment.
    """
    env = dict(os.environ)
    env.update(env_extra)
    env["DSH_PERMISSION_MODE"] = permission_mode_for_tier(member.tier)
    completed = subprocess.run(  # noqa: S603
        [settings.DSH_BINARY, "--profile", member.profile or settings.DSH_PROFILE, "headless", prompt],
        cwd=str(island),
        env=env,
        capture_output=True,
        text=True,
        timeout=settings.DSH_RUN_TIMEOUT_SECONDS,
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


@shared_task
def run_agent_member(run_id):
    """Execute one `AgentRun`. Idempotent: a run that is not `pending` is skipped."""
    try:
        run = (
            AgentRun.objects.select_related("member", "member__bot_user", "member__service_token")
            .filter(pk=run_id)
            .first()
        )
    except Exception as e:
        log_exception(e)
        return
    if run is None or run.status != AgentRunStatusEnum.PENDING.value:
        return

    try:
        _execute(run)
    except Exception as e:
        log_exception(e)
        run.status = AgentRunStatusEnum.FAILED.value
        run.error = str(e)
        run.finished_at = timezone.now()
        AgentRun.objects.filter(pk=run.pk).update(
            status=run.status,
            error=run.error,
            finished_at=run.finished_at,
        )
        _post_failure_comment(run)


def _execute(run):
    member = run.member
    issue = run.issue
    project = run.project
    workspace = run.workspace

    island = project_directory(workspace, project)
    island.mkdir(parents=True, exist_ok=True)

    # A config-scoped service token, read at run time and handed to the child's
    # environment only. Never written anywhere (design §2 「token 不落盘」).
    token = getattr(member.service_token, "token", "") or ""

    # 丙档第一轮 = 「是丙档」且「还没有计划」。`plan` 非空即表示已过闸。
    is_plan_round = member.tier == AgentTierEnum.LEDGER.value and not run.plan

    if is_plan_round:
        prompt = build_plan_prompt(member=member, project=project, issue=issue, island=island)
    elif run.plan:
        prompt = build_execute_prompt(
            member=member, project=project, issue=issue, island=island, plan=run.plan
        )
    else:
        prompt = build_execute_prompt(
            member=member, project=project, issue=issue, island=island, plan=""
        )

    AgentRun.objects.filter(pk=run.pk).update(
        status=AgentRunStatusEnum.RUNNING.value, started_at=timezone.now()
    )

    before_files = snapshot_tree(island)
    before_sessions = session_dirs()

    env_extra = {
        "PLANE_API_KEY": token,
        "PLANE_WORKSPACE_SLUG": workspace.slug,
        # The public API lives at the bare origin — the `/926` prefix is the web
        # app's, not the API's.
        "PLANE_BASE_URL": os.environ.get("PLANE_BASE_URL_FOR_AGENTS", "http://localhost:8000"),
    }

    try:
        exit_code, stdout, stderr = _run_headless(
            prompt=prompt, island=island, member=member, env_extra=env_extra
        )
    except subprocess.TimeoutExpired:
        exit_code, stdout, stderr = 124, "", f"Timed out after {settings.DSH_RUN_TIMEOUT_SECONDS}s"
    except OSError as e:
        # `FileNotFoundError`（PATH 里没有 `dsh`）是这里最可能的部署事故，也正是
        # Task 8 Step 7 要防的那一个；它必须和别的失败一样，在卡片上说话
        # （设计 §5「失败也回程」）。127 = shell 的「命令找不到」。
        exit_code = 127
        stdout = ""
        stderr = f"Could not start {settings.DSH_BINARY}: {e}"

    artifacts = diff_snapshot(before_files, snapshot_tree(island))
    session_ref = new_session_ref(before_sessions, session_dirs())

    # A weak form of "watch it run": the raw output on disk, next to the island's
    # content, so a failure can be read after the fact. No streaming, no session
    # id (design §6 「观战」). Written *after* the artifact diff above on purpose —
    # the log would otherwise be counted as one of the run's own artifacts.
    try:
        log_dir = island / ".agent-runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{run.id}.log").write_text(
            f"# exit_code={exit_code}\n\n## stdout\n{stdout}\n\n## stderr\n{stderr}\n",
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Could not write the agent run log for %s: %s", run.id, e)

    run.exit_code = exit_code
    run.artifacts = artifacts
    run.session_ref = session_ref
    run.finished_at = timezone.now()

    if exit_code == 0 and stdout.strip():
        if is_plan_round:
            # 第一轮只出计划：贴成评论，停在「等人批准」。
            run.plan = stdout.strip()
            run.status = AgentRunStatusEnum.AWAITING_APPROVAL.value
            run.error = ""
            run.save(update_fields=["plan", "status", "error", "exit_code", "artifacts", "session_ref", "finished_at"])
            post_bot_comment(
                member=member,
                issue=issue,
                html=(
                    "<p><strong>计划（还没动手）</strong> —— 回复「批准」我就开始。</p>"
                    f"<pre>{run.plan}</pre>"
                ),
            )
            return

        run.status = AgentRunStatusEnum.SUCCEEDED.value
        run.error = ""
        run.save(update_fields=["status", "error", "exit_code", "artifacts", "session_ref", "finished_at"])
        # 回程第一条：成功也要以 bot 身份说话 —— 结论 + 产物路径清单。没有它，
        # 一次成功的运行在卡片上是静默的（设计 §5 回程三条第 1/3 条）。
        post_bot_comment(member=member, issue=issue, html=_success_html(stdout.strip(), artifacts))
    else:
        run.status = AgentRunStatusEnum.FAILED.value
        run.error = (stderr.strip() or stdout.strip() or f"exit code {exit_code}")[:5000]
        run.save(update_fields=["status", "error", "exit_code", "artifacts", "session_ref", "finished_at"])

    group = state_group_for_run_outcome(run.status, tier=member.tier)
    if group:
        set_issue_state_group(issue, group)

    if run.status == AgentRunStatusEnum.FAILED.value:
        # 模型这时候多半已经死了，不能指望它自己说（设计 §5）。
        _post_failure_comment(run)
