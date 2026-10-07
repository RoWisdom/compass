# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""项目镜像目录（= AI 成员的沙箱岛）按 **Plane 项目 id** 命名，不按项目名。

为什么（两条，都是真的）：

  · **沙箱越界**：``project_directory`` 既是项目镜像目录，也是 AI 成员的沙箱岛
    （设计 §2：DSH 的写根、跑前跑后 diff 的那棵树）。旧规则用**净化后的项目名**，
    而净化发生在 DB 唯一约束**之后** —— ``官网/新版`` 与 ``官网-新版`` 是两个合法
    不同的项目，却折进同一个目录 ⇒ 项目 A 上唤醒的 AI ``cwd`` 是那个共用目录、
    权限 ``workspace-write`` ⇒ **它读并覆盖项目 B 的镜像页面**。
  · **与用户自己的 vault 目录冲突**：镜像写进 ``2-项目/<项目名>/``，那正是用户
    **手工命名**的项目文件夹所在的地方。id 目录不可能与手工命名的文件夹同名。

``project_directory`` 的**签名不变**（``plane.bgtasks.agent_run_task`` 与
``tests/unit/bg_tasks/test_agent_run_task.py`` 都在按原样调它）—— 变的只是目录名。

本文件还锁住搬迁既有目录的管理命令（``rename_project_mirror_directories``）：
默认 dry-run 一字不动，``--apply`` 才真搬，且可重复执行。
"""

from io import StringIO

import pytest
from django.core.management import call_command

from plane.db.models import Project
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
class TestProjectDirectoryIsKeyedById:
    def test_the_directory_is_the_project_id_not_its_name(self, workspace, create_user):
        """目录 = 项目 id。名字再怎么净化都不参与。"""
        project = _project(workspace, create_user, "面料交易", "MIR1")

        assert project_directory(workspace, project).name == str(project.id)
        assert project_directory(workspace, project).name != "面料交易"

    def test_two_projects_whose_names_sanitize_alike_get_different_directories(self, workspace, create_user):
        """缺陷本身：``官网/新版`` 与 ``官网-新版`` 曾是同一个目录 —— 现在不可能。

        这两个名字在 ``(name, workspace)`` 唯一约束下**都是合法的**（它们不相等），
        但净化后撞在一起（``/`` 被换成 ``-``）。旧规则下两次 ``project_directory``
        返回同一个目录 —— 这条测试在旧规则下必须是**红的**。
        """
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
        old = root / (_sanitize_name(project.name or "") or str(project.id))
        (old / "sub").mkdir(parents=True)
        (old / "sub" / "x.md").write_text("正文", encoding="utf-8")
        return root, project, old

    def test_a_dry_run_touches_nothing(self, workspace, create_user, tmp_path, monkeypatch):
        root, project, old = self._seed(workspace, create_user, tmp_path, monkeypatch, "面料交易", "MOV1")

        call_command("rename_project_mirror_directories", stdout=StringIO())

        assert (old / "sub" / "x.md").is_file(), "默认 dry-run 不得动盘"
        assert not (root / str(project.id)).exists()

    def test_apply_moves_the_directory_and_is_idempotent(self, workspace, create_user, tmp_path, monkeypatch):
        root, project, old = self._seed(workspace, create_user, tmp_path, monkeypatch, "面料交易", "MOV2")
        new = root / str(project.id)

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
