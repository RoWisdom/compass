# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""项目页正文端点必须把**工作区字段**档的镜像根走到最后一步。

镜像根的优先级是 **workspace 字段 > 环境变量 > 内置默认**（见
``plane/utils/markdown_storage.py`` 的两个 getter）。这条优先级在单元层
（``tests/unit/utils/test_markdown_storage_root_precedence.py``）已经钉死，但那条测试
是**直接调解析器、喂一个 duck-typed 对象** —— 它证明解析函数对，证明不了**写路径真的
把字段传了下去**。

写路径在 ``app/views/page/base.py``：``_project_mirror_root(project_id)`` 查出项目所属
工作区、把它交给 ``get_markdown_root``，再作为**必填的 ``root=``** 下传给
``write_page_markdown`` / ``move_page_markdown`` / ``delete_page_markdown``。项目页的
**正文写入**端点是 ``PagesDescriptionViewSet.partial_update``
（``PATCH …/projects/<project_id>/pages/<page_id>/description/``），它经
``_write_page_mirror`` 走到 ``_project_mirror_root``。

**这里要防的正是「字段没传下去」**：把 ``_project_mirror_root`` 改成

    return get_markdown_root(None)          # 无视字段，退回 env / 内置默认

不会抛 ``TypeError``（``root=`` 这个关键字还在），现有全部镜像契约测试也仍然**全绿** ——
因为 autouse 夹具 ``tests/conftest.py::isolate_markdown_mirror`` 只 ``setenv``，从不落
工作区字段，env 档与字段档在那些测试里**恰好是同一个目录**。于是镜像会**静默**改落到
``~/projects``。所以这条测试落点必须**同时**断两半：落在字段目录下 **且** 不在 env 根下；
少了后半句，一个「无视字段、照写 env 根」的实现能让它空转通过。

夹具链照 ``tests/contract/app/test_wiki_page_description_app.py`` 的
``project`` / ``linked_wiki_page`` / ``session_client`` 抄 —— 同一套
workspace + project + ProjectPage 形状，只是把落点的来源从 env 换成工作区字段。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage


@pytest.fixture
def project(workspace, create_user):
    """本工作区的一个项目，成员是 ADMIN —— 正文写端点放行。"""
    project = Project.objects.create(
        name="镜像项目",
        identifier="MIR",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def project_page(workspace, project, create_user):
    """挂在 ``project`` 下的一页 —— ``PagesDescriptionViewSet`` 的查询与
    ``ProjectPagePermission`` 都要靠这条 ProjectPage 链接才解析得到。"""
    page = Page.objects.create(
        workspace=workspace,
        name="字段档正文页",
        owned_by=create_user,
        access=Page.PUBLIC_ACCESS,
    )
    ProjectPage.objects.create(
        workspace=workspace,
        project=project,
        page=page,
        created_by_id=create_user.id,
        updated_by_id=create_user.id,
    )
    return page


@pytest.mark.contract
class TestProjectPageMirrorUsesTheWorkspaceField:
    @pytest.mark.django_db
    def test_mirror_lands_under_the_field_root_not_the_env_root(
        self, session_client, workspace, project, project_page, isolate_markdown_mirror, tmp_path
    ):
        # 字段目录与 autouse 夹具钉的 env 根（``tmp_path / "markdown-mirror"``）
        # 是 tmp_path 下**两个不同的子目录** —— 后半句断言才有意义。
        field_root = tmp_path / "field-configured-projects"
        workspace.project_markdown_path = str(field_root)
        workspace.save(update_fields=["project_markdown_path"])

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{project_page.id}/description/",
            {"description_html": "<p>字段档镜像</p>"},
            format="json",
        )

        # 先断言请求真的进了 handler 且写成功：404 / 权限被拒时，下面的落点断言
        # 会因为「哪里都没有文件」而**同时**通过两半 —— 测试会空转。
        assert response.status_code == status.HTTP_200_OK

        written = list(field_root.rglob("*.md"))
        assert len(written) == 1, f"字段档应当写出恰好一份镜像：{written!r}"
        # 目录名是**项目 id**那一层（用名字会撞车，id 不会）—— 证明镜像落进了字段根下项目自己的那一层，而不是别处。
        assert written[0].parent == field_root / str(project.id)
        assert written[0].name == "字段档正文页.md"
        assert "字段档镜像" in written[0].read_text(encoding="utf-8")

        # 两半都必须断：只断「字段目录下有」的话，一个无视字段、把两份目录当成同一处的
        # 实现也能通过。正半 —— 落在字段根下：
        assert written[0].is_relative_to(field_root)
        # 反半 —— **没有**落在 env 根下（夹具返回的就是 env 根）：
        assert not written[0].is_relative_to(isolate_markdown_mirror)
        assert list(isolate_markdown_mirror.rglob("*.md")) == [], "镜像不得落到 env 根下"
