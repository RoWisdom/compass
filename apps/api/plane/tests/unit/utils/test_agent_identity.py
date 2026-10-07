# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from django.db import IntegrityError

from plane.db.models import AgentDefinition, AgentMember, ProjectMember, User, WorkspaceMember
from plane.utils.agent_identity import deploy, retire


def _agent_users():
    """这个测试库里 ``deploy`` 造出来的 bot 用户。用户名一律以 ``agent_`` 打头。"""
    return User.objects.filter(is_bot=True, username__startswith="agent_")


@pytest.mark.django_db
def test_deploy_builds_the_whole_identity(workspace, project, create_user):
    """设计 §2：一行成员 = bot 用户 + 工作区成员 + 项目成员 + service token。"""
    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", created_by_id=create_user.id
    )
    member = deploy(definition=definition, project=project, created_by_id=create_user.id)

    assert member.definition_id == definition.id
    assert member.bot_user.is_bot is True
    assert member.service_token_id is not None
    assert member.service_token.is_service is True
    assert WorkspaceMember.objects.filter(workspace=workspace, member_id=member.bot_user_id).exists()
    assert ProjectMember.objects.filter(project=project, member_id=member.bot_user_id).exists()


@pytest.mark.django_db
def test_a_second_deploy_leaves_no_orphan_bot_user(workspace, project, create_user):
    """``deploy`` **不是幂等**的：第二次撞 ``(project, definition)`` 唯一约束。

    但撞约束时它**必须整体回滚** —— 那次 INSERT 之前已经建好了 bot 用户、工作区成员、
    项目成员。生产是 autocommit，没有外层事务替它们收尸 ⇒ 不套 atomic 就会留下一个
    **没有成员行的孤儿 bot 用户**（连带两条成员关系）。

    去掉 ``deploy`` 里的 ``transaction.atomic()``，这条测试会红（连接被
    ``mark_for_rollback_on_error`` 染脏，读库即 ``TransactionManagementError``）。
    """
    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", created_by_id=create_user.id
    )
    deploy(definition=definition, project=project, created_by_id=create_user.id)
    assert _agent_users().count() == 1

    with pytest.raises(IntegrityError):
        deploy(definition=definition, project=project, created_by_id=create_user.id)

    assert _agent_users().count() == 1
    assert AgentMember.objects.filter(project=project, definition=definition).count() == 1
    assert ProjectMember.objects.filter(project=project, member__is_bot=True).count() == 1


@pytest.mark.django_db
def test_retire_takes_the_project_identity_and_the_token(workspace, project, create_user):
    """设计 §6②：「移除」按完不能还留着一扇能发评论的后门。"""
    definition = AgentDefinition.objects.create(
        workspace_id=workspace.id, name="需求分析", created_by_id=create_user.id
    )
    member = deploy(definition=definition, project=project, created_by_id=create_user.id)
    token = member.service_token

    retire(member)

    assert not ProjectMember.objects.filter(project=project, member_id=member.bot_user_id).exists()
    token.refresh_from_db()
    assert token.is_active is False
    assert token.expired_at is not None
    # bot 用户本身留着 —— 它不会连带 CASCADE 掉 AgentRun 历史
    assert User.objects.filter(pk=member.bot_user_id).exists()
