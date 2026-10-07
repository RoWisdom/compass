# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The gate and the anti-loop rule — one signal, because they are one thing.

设计 §5: the first round's plan comment is authored by the bot, so the anti-loop
rule («an AI's action never wakes anything») leaves it sitting quietly on the card
until a human replies. The human's reply is not a bot's, so it does wake the second
round. **No second mechanism is needed.**

Registered from ``plane.db.apps.DbConfig.ready()``.
"""

# Django imports
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from plane.bgtasks.agent_run_task import run_agent_member
from plane.db.models import AgentRun, AgentRunStatusEnum, IssueComment
from plane.utils.exception_logger import log_exception

#: A human approves a ledger member's plan by replying with a comment that starts
#: with this. Zero new UI, and it leaves a natural audit trail: who, when, in their
#: own words (design §4 「批准怎么表达」).
APPROVAL_PREFIX = "批准"


@receiver(post_save, sender=IssueComment)
def agent_approval_on_comment(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        _maybe_requeue(instance)
    except Exception as e:
        # A comment must never fail because the agent plumbing did.
        log_exception(e)


def _maybe_requeue(comment):
    actor = comment.actor
    # 防环。One line, and it is the whole rule (design §5).
    if actor is None or actor.is_bot:
        return
    if not (comment.comment_stripped or "").strip().startswith(APPROVAL_PREFIX):
        return

    run = (
        AgentRun.objects.filter(
            issue_id=comment.issue_id, status=AgentRunStatusEnum.AWAITING_APPROVAL.value
        )
        .order_by("-created_at")
        .first()
    )
    if run is None:
        return

    run.status = AgentRunStatusEnum.PENDING.value
    run.save(update_fields=["status"])

    # The comment is saved inside the request's transaction, so enqueue only after
    # it commits — otherwise the worker can look for a run row that is not visible
    # yet. (The rest of the fork enqueues straight after `serializer.save()`, which
    # is fine for tasks that only read the object they were handed; this one reads
    # the DB.)
    run_id = str(run.id)
    transaction.on_commit(lambda: run_agent_member.delay(run_id))
