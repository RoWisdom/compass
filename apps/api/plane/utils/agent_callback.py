# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""回程：把一次运行的结果写回 Plane。见设计 §5。

**唯一碰流程的写者就是这里** —— 成员自己碰不到状态（它的公开 API 被
``plane/api/views/issue.py`` 的守卫挡着），所以「AI 的动作不触发唤醒」这条防环
规则在本模块之外天然成立。
"""

# Module imports
from plane.db.models import AgentRunStatusEnum, IssueComment, State, StateGroup


def post_bot_comment(*, member, issue, html: str) -> IssueComment:
    """Post a comment on ``issue`` authored by the member's bot user.

    Deliberately not going through ``IssueCommentViewSet.create``: that path needs
    a request, a permission check against the *human* caller and a project
    membership lookup, none of which apply to a server-side callback. What it does
    have to replicate is the authorship — ``actor`` **and** ``created_by`` both
    point at the bot user, because the frontend decides "this is an AI" from the
    actor (design §5: 本版本没有 actor_type).
    """
    comment = IssueComment(
        project_id=issue.project_id,
        workspace_id=issue.workspace_id,
        issue_id=issue.id,
        actor_id=member.bot_user_id,
        comment_html=html,
    )
    # Set it on the instance *before* saving, not merely as a ``save()`` kwarg:
    # ``IssueComment.save`` snapshots ``self.created_by_id`` into the ``Description``
    # row it creates alongside the comment, so this is what makes that row read
    # "the bot" too. As a kwarg alone it would be applied only after the snapshot.
    comment.created_by_id = member.bot_user_id
    # created_by_id explicitly too: there is no request user to infer it from, and
    # the audit trail should read "the bot", not "nobody".
    comment.save(created_by_id=member.bot_user_id)
    # ``IssueComment.save`` saves a second time internally to attach the description,
    # and that inner save defaults ``created_by`` back to None in memory (the row is
    # untouched — only ``description_id`` is in its ``update_fields``). Re-read so the
    # returned object matches the row we just wrote; Task 8 hands it straight on.
    comment.refresh_from_db()
    return comment


def set_issue_state_group(issue, group: str) -> bool:
    """Move the issue to the first state of ``group``. ``False`` when there is none.

    Only two callers' worth of groups are ever passed (``started`` on success,
    ``unstarted`` on failure) and only for the ledger tier — see
    ``state_group_for_run_outcome``. A project with no state in that group is left
    alone rather than silently falling back to some other state.
    """
    state = (
        State.objects.filter(project_id=issue.project_id, group=group)
        .order_by("sequence")
        .first()
    )
    if state is None:
        return False
    issue.state_id = state.id
    issue.save(update_fields=["state"])
    return True


def state_group_for_run_outcome(status: str, *, tier: str):
    """The state group Plane should move the card to, or ``None`` to leave it alone.

    设计 §4 的档位表 + §5 的回程三条合起来给出这条规则：**只有丙档（动台账）
    才允许 Plane 搬卡**。甲/乙 被唤醒后卡片状态必须一字不动 —— 这是设计 §6
    剧本第 7 步的判据。等待批准不是一个「结果」，所以它不搬。
    """
    if tier != "ledger":
        return None
    if status == AgentRunStatusEnum.SUCCEEDED.value:
        return StateGroup.STARTED.value
    if status == AgentRunStatusEnum.FAILED.value:
        return StateGroup.UNSTARTED.value
    return None
