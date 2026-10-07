# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""项目镜像目录（= AI 成员的沙箱岛）按 **Plane 项目 identifier**（净化后）命名，
不按项目名、也不按 UUID 主键。

为什么（两条，都是真的）：

  · **沙箱越界**：``project_directory`` 既是项目镜像目录，也是 AI 成员的沙箱岛
    （设计 §2：DSH 的写根、跑前跑后 diff 的那棵树）。旧规则用**净化后的项目名**，
    而净化发生在 DB 唯一约束**之后** —— ``官网/新版`` 与 ``官网-新版`` 是两个合法
    不同的项目，却折进同一个目录 ⇒ 项目 A 上唤醒的 AI ``cwd`` 是那个共用目录、
    权限 ``workspace-write`` ⇒ **它读并覆盖项目 B 的镜像页面**。
  · **与用户自己的 vault 目录冲突**：镜像写进 ``2-项目/<项目名>/``，那正是用户
    **手工命名**的项目文件夹所在的地方。identifier 目录不可能与手工命名的文件夹
    同名。

``project_directory`` 的**签名不变**（``plane.bgtasks.agent_run_task`` 与
``tests/unit/bg_tasks/test_agent_run_task.py`` 都在按原样调它）—— 变的只是目录名。

本文件还锁住搬迁既有目录的管理命令（``rename_project_mirror_directories``）：
默认 dry-run 一字不动，``--apply`` 才真搬，且可重复执行。
"""

from io import StringIO

import pytest
from django.core.management import call_command

from plane.db.models import AgentMember, AgentRun, AgentRunStatusEnum, AgentTierEnum, Project
from plane.utils.markdown_storage import (
    MARKDOWN_STORAGE_PATH_ENV,
    _sanitize_name,
    project_directory,
)

pytestmark = pytest.mark.django_db


def _project(workspace, create_user, name, identifier):
    """一个 `workspace` 下的项目 —— 名字与标识符都显式给，测试自己控制净化结果。"""
    return Project.objects.create(
        name=name,
        identifier=identifier,
        workspace=workspace,
        created_by=create_user,
    )


@pytest.mark.unit
class TestProjectDirectoryIsKeyedByIdentifier:
    def test_the_directory_is_the_project_identifier_not_its_name(self, workspace, create_user, tmp_path, monkeypatch):
        """目录 = 项目 identifier（净化后）。名字再怎么净化都不参与。"""
        monkeypatch.setenv(MARKDOWN_STORAGE_PATH_ENV, str(tmp_path / "mirror-root"))
        project = _project(workspace, create_user, "面料交易", "MIR1")

        assert project_directory(workspace, project).name == "MIR1"
        assert project_directory(workspace, project).name != "面料交易"

    def test_an_empty_identifier_falls_back_to_the_project_id(self, workspace, create_user, tmp_path, monkeypatch):
        """identifier 为空时退回项目 id —— 否则 ``root / ""`` 塌成镜像根本身。

        正常走不到这条路：identifier 在 DRF 上是必填（``blank=False``），UI 与 API 都
        造不出空的。但 DB 列不过滤空串，而 ``project_directory`` 同时是 AI 成员的沙箱
        岛 —— 一旦塌成根，整个镜像根都落进沙箱。退回口径与
        ``rename_project_mirror_directories``（都用项目 id）一致，两处不漂。
        """
        monkeypatch.setenv(MARKDOWN_STORAGE_PATH_ENV, str(tmp_path / "mirror-root"))
        project = _project(workspace, create_user, "无标识项目", "")

        assert project_directory(workspace, project).name == str(project.id)

    def test_two_projects_whose_names_sanitize_alike_get_different_directories(
        self, workspace, create_user, tmp_path, monkeypatch
    ):
        """缺陷本身：``官网/新版`` 与 ``官网-新版`` 曾是同一个目录 —— 现在不可能。

        这两个名字在 ``(name, workspace)`` 唯一约束下**都是合法的**（它们不相等），
        但净化后撞在一起（``/`` 被换成 ``-``）。旧规则下两次 ``project_directory``
        返回同一个目录 —— 这条测试在旧规则下必须是**红的**。
        """
        monkeypatch.setenv(MARKDOWN_STORAGE_PATH_ENV, str(tmp_path / "mirror-root"))
        slash = _project(workspace, create_user, "官网/新版", "MIR2")
        dash = _project(workspace, create_user, "官网-新版", "MIR3")

        # 前置：两个项目真的并存（DB 放行），且净化后确实同名 —— 这正是缺陷的形状。
        assert slash.name != dash.name
        assert _sanitize_name(slash.name) == _sanitize_name(dash.name) == "官网-新版"

        assert project_directory(workspace, slash) != project_directory(workspace, dash)


@pytest.mark.unit
class TestRenameProjectMirrorDirectoriesCommand:
    """既有镜像目录的搬迁命令：默认 dry-run，``--apply`` 才真搬，可重复执行。"""

    def _seed(self, workspace, create_user, tmp_path, monkeypatch, name, identifier):
        """临时 root + 一个项目 + ``<旧目录名>/sub/x.md``，返回 (root, project, old)。"""
        root = tmp_path / "mirror-root"
        # 与 ``test_markdown_storage_root_precedence.py`` 同一套设根方式。
        monkeypatch.setenv(MARKDOWN_STORAGE_PATH_ENV, str(root))

        project = _project(workspace, create_user, name, identifier)
        # Hard-coded on purpose: this is the *historical* rule (sanitize the name,
        # fall back to the id). Recomputing it with the command's own expression
        # would let a wrong reconstruction inside the command stay green.
        old = root / "面料交易"
        (old / "sub").mkdir(parents=True)
        (old / "sub" / "x.md").write_text("正文", encoding="utf-8")
        return root, project, old

    def test_a_dry_run_touches_nothing(self, workspace, create_user, tmp_path, monkeypatch):
        root, project, old = self._seed(workspace, create_user, tmp_path, monkeypatch, "面料交易", "MOV1")

        call_command("rename_project_mirror_directories", stdout=StringIO())

        assert (old / "sub" / "x.md").is_file(), "默认 dry-run 不得动盘"
        assert not (root / project.identifier).exists()

    def test_apply_moves_the_directory_and_is_idempotent(self, workspace, create_user, tmp_path, monkeypatch):
        root, project, old = self._seed(workspace, create_user, tmp_path, monkeypatch, "面料交易", "MOV2")
        new = root / project.identifier

        call_command("rename_project_mirror_directories", "--apply", stdout=StringIO())

        assert new.is_dir()
        assert (new / "sub" / "x.md").is_file(), "文件必须跟着目录一起搬"
        assert (new / "sub" / "x.md").read_text(encoding="utf-8") == "正文"
        assert not old.exists()

        # 再跑一次 = 空操作（幂等）：盘上不多不少。
        before = sorted(p.relative_to(root) for p in root.rglob("*"))
        call_command("rename_project_mirror_directories", "--apply", stdout=StringIO())
        after = sorted(p.relative_to(root) for p in root.rglob("*"))
        assert before == after

    def test_a_shared_old_directory_is_refused_never_arbitrated(
        self, workspace, create_user, tmp_path, monkeypatch
    ):
        """两个项目共用一个旧目录时**拒绝搬**，绝不按 ``created_at`` 仲裁。

        净化发生在 ``(name, workspace)`` 唯一约束**之后**，所以 ``官网/新版`` 与
        ``官网-新版`` 这两个合法项目折叠进同一个旧目录，两边的镜像交织在一起。
        搬进任一个项目的 identifier 目录 = 另一个项目的页面进了它的沙箱岛 —— 正是改名要
        消灭的越界。旧代码会把整个目录搬进先建的那个项目的 id 目录，这条测试在那时
        是**红的**。
        """
        root = tmp_path / "mirror-root"
        monkeypatch.setenv(MARKDOWN_STORAGE_PATH_ENV, str(root))

        slash = _project(workspace, create_user, "官网/新版", "SHR1")
        dash = _project(workspace, create_user, "官网-新版", "SHR2")
        # 前置：两个项目真的并存，且净化后确实同名 —— 这正是「共用一个旧目录」的形状。
        assert slash.name != dash.name
        assert _sanitize_name(slash.name) == _sanitize_name(dash.name) == "官网-新版"

        # 盘上只有一个共用的旧目录，里面放一个文件。
        old = root / "官网-新版"
        old.mkdir(parents=True)
        (old / "x.md").write_text("正文", encoding="utf-8")

        # dry-run：两个项目**都**走 [shared] 分支，盘上一字未动。
        out = StringIO()
        call_command("rename_project_mirror_directories", stdout=out)
        assert out.getvalue().count("[shared]") == 2
        assert (old / "x.md").is_file(), "共用的旧目录不得被搬动"
        assert not (root / slash.identifier).exists()
        assert not (root / dash.identifier).exists()

        # --apply：仍然什么都不动 —— 这一条是本次修复的理由。旧代码会把目录搬进
        # 先建的那个项目的 identifier 目录（后者的页面就此进了前者的沙箱岛）。
        call_command("rename_project_mirror_directories", "--apply", stdout=StringIO())
        assert (old / "x.md").is_file(), "共用的旧目录不得被搬动"
        assert not (root / slash.identifier).exists()
        assert not (root / dash.identifier).exists()

    def test_a_conflicting_target_directory_refuses_to_merge(
        self, workspace, create_user, tmp_path, monkeypatch
    ):
        """目标 identifier 目录**已存在**时拒绝搬，绝不合并两棵树。

        ``new.exists()`` 这道闸是这条命令唯一阻止「把两个项目的镜像合成一个目录」的
        地方，而它会真动用户的 vault —— 闸不该裸着。盘上同时有 ``<旧名>/x.md`` 与
        ``<identifier>/y.md``（目标已存在 ⇒ 冲突）。
        """
        root, project, old = self._seed(workspace, create_user, tmp_path, monkeypatch, "面料交易", "CFL1")
        new = root / project.identifier
        new.mkdir(parents=True)
        (new / "y.md").write_text("另一棵树", encoding="utf-8")

        # dry-run：走 [conflict] 分支，两棵树都原样。
        out = StringIO()
        call_command("rename_project_mirror_directories", stdout=out)
        assert "[conflict]" in out.getvalue()
        assert (old / "sub" / "x.md").is_file(), "旧名目录不得被搬走"
        assert (new / "y.md").is_file(), "已存在的 identifier 目录不得被动"

        # --apply：仍然什么都不动 —— 不搬、不合并。
        call_command("rename_project_mirror_directories", "--apply", stdout=StringIO())
        assert (old / "sub" / "x.md").is_file(), "旧名目录不得被搬走"
        assert (new / "y.md").is_file(), "已存在的 identifier 目录不得被动"
        assert not (new / "sub").exists(), "两棵树不得合并"

    def test_apply_refuses_while_an_agent_run_is_in_flight(
        self, workspace, create_user, create_bot_user, project, create_issue, tmp_path, monkeypatch
    ):
        """AI 正在跑时 ``--apply`` **拒绝整次搬迁** —— 岛就是那个子进程的 ``cwd``。

        ``old.rename(new)`` 会把一个 ``running`` 的 run 的**岛**从它脚下搬走，后果是
        **静默的错结果**：``snapshot_tree`` 对已不存在的路径返回 ``{}`` ⇒ ``diff_snapshot``
        返回 ``[]`` ⇒ 该 run 仍被记成 SUCCEEDED、评论里写着「这次没有新增或改动的文件。」。
        这条锁两件事：在跑时**一个字节都不动**，跑完了（``succeeded``）才真的搬。
        """
        root, seeded, old = self._seed(workspace, create_user, tmp_path, monkeypatch, "面料交易", "RUN1")

        member = AgentMember.objects.create(
            name="需求分析",
            tier=AgentTierEnum.READONLY.value,
            project_id=project.id,
            workspace_id=workspace.id,
            bot_user_id=create_bot_user.id,
        )
        run = AgentRun.objects.create(
            member=member,
            issue_id=create_issue.id,
            project_id=project.id,
            workspace_id=workspace.id,
            status=AgentRunStatusEnum.RUNNING.value,
        )

        # 在跑：拒绝整次 apply，盘上一字未动。
        out = StringIO()
        call_command("rename_project_mirror_directories", "--apply", stdout=out)
        assert "[refused]" in out.getvalue()
        assert (old / "sub" / "x.md").is_file(), "在跑的 run 会拒整次搬迁，旧目录不得被动"
        assert not (root / seeded.identifier).exists(), "拒绝时不得搬出任何 identifier 目录"

        # 跑完：这次真的搬 —— 证明闸只挡在跑的那些。
        run.status = AgentRunStatusEnum.SUCCEEDED.value
        run.save(update_fields=["status"])
        new = root / seeded.identifier
        call_command("rename_project_mirror_directories", "--apply", stdout=StringIO())
        assert new.is_dir(), "没有在跑的 run 时，--apply 必须真的搬"
        assert (new / "sub" / "x.md").is_file()
        assert not old.exists()
