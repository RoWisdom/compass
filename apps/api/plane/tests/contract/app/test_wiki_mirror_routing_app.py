# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""wiki 页面的镜像是**三分支**的（设计 §3.3）。

`3-Wiki` 下的文件夹成为 Plane 的**集合**，所以「一个页面该落到哪棵树」不再只看项目：

| 页面 | 落点 |
|---|---|
| 有集合 | `3-Wiki/<集合名>/…` |
| 无集合、有项目 | `2-项目/<项目名>/…`（逐字不变，这里有回归锁） |
| 都没有 | 不落盘 + `logger.warning`（Phase 1B 的 §2.3c 裁定，逐字不变） |

第三行是**旧行为**，前两行的**顺序**是本轮的裁定：集合优先于项目。
"""

import pytest

from plane.db.models import Page, PageCollection, Project, ProjectPage


@pytest.fixture
def wiki_page(db, workspace, create_user):
    """一个已收录进 Wiki 的页面，还没有集合、没有项目。"""
    return Page.objects.create(
        workspace=workspace,
        name="路由测试页",
        description_html="<p>原文</p>",
        owned_by=create_user,
        is_global=True,
    )


@pytest.mark.contract
class TestMirrorRouting:
    @pytest.mark.django_db
    def test_page_with_a_collection_mirrors_under_the_wiki_root(self, isolate_markdown_mirror, wiki_page, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="Claude Code", owned_by=create_user)
        Page.objects.filter(id=wiki_page.id).update(collection=collection)

        from plane.app.views.page.collection import _mirror_wiki_page

        _mirror_wiki_page(Page.objects.get(id=wiki_page.id), "<p>新正文</p>")

        written = isolate_markdown_mirror.parent / "3-Wiki" / "Claude Code" / "路由测试页.md"
        assert written.is_file()
        assert written.read_text(encoding="utf-8").endswith("新正文")
        # 并且**没有**落进项目树
        assert not (isolate_markdown_mirror / "Claude Code").exists()

    @pytest.mark.django_db
    def test_page_with_a_project_only_still_mirrors_under_the_projects_root(
        self, isolate_markdown_mirror, wiki_page, workspace, create_user
    ):
        """回归锁：没有集合的页面路径一个字都不能变。"""
        project = Project.objects.create(workspace=workspace, name="面料交易", identifier="FAB", created_by=create_user)
        ProjectPage.objects.create(workspace=workspace, project=project, page=wiki_page, created_by=create_user)

        from plane.app.views.page.collection import _mirror_wiki_page

        _mirror_wiki_page(Page.objects.get(id=wiki_page.id), "<p>新正文</p>")

        written = isolate_markdown_mirror / "面料交易" / "路由测试页.md"
        assert written.is_file()
        assert not (isolate_markdown_mirror.parent / "3-Wiki").exists()

    @pytest.mark.django_db
    def test_collection_beats_project_when_both_are_present(self, isolate_markdown_mirror, wiki_page, workspace, create_user):
        project = Project.objects.create(workspace=workspace, name="面料交易", identifier="FAB", created_by=create_user)
        ProjectPage.objects.create(workspace=workspace, project=project, page=wiki_page, created_by=create_user)
        collection = PageCollection.objects.create(workspace=workspace, name="Claude Code", owned_by=create_user)
        Page.objects.filter(id=wiki_page.id).update(collection=collection)

        from plane.app.views.page.collection import _mirror_wiki_page

        _mirror_wiki_page(Page.objects.get(id=wiki_page.id), "<p>新正文</p>")

        assert (isolate_markdown_mirror.parent / "3-Wiki" / "Claude Code" / "路由测试页.md").is_file()
        assert not (isolate_markdown_mirror / "面料交易").exists()

    @pytest.mark.django_db
    def test_page_with_neither_mirrors_nowhere_and_logs(self, isolate_markdown_mirror, wiki_page, caplog):
        from plane.app.views.page.collection import _mirror_wiki_page

        with caplog.at_level("WARNING"):
            _mirror_wiki_page(Page.objects.get(id=wiki_page.id), "<p>新正文</p>")

        assert not (isolate_markdown_mirror.parent / "3-Wiki").exists()
        assert not isolate_markdown_mirror.exists()
        assert any("Skipping markdown mirror" in record.message for record in caplog.records)

    @pytest.mark.django_db
    def test_rename_moves_the_file_inside_the_wiki_tree(self, isolate_markdown_mirror, wiki_page, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="Claude Code", owned_by=create_user)
        Page.objects.filter(id=wiki_page.id).update(collection=collection)

        from plane.app.views.page.collection import _mirror_wiki_page, _move_wiki_page_mirror

        _mirror_wiki_page(Page.objects.get(id=wiki_page.id), "<p>正文</p>")
        Page.objects.filter(id=wiki_page.id).update(name="改过名")
        # 第三个参数是 `save()` **之前**的集合 id（见修复 B）：本测试只改名、不换集合，
        # 所以旧集合就是新集合。
        _move_wiki_page_mirror(Page.objects.get(id=wiki_page.id), "路由测试页", collection.id)

        folder = isolate_markdown_mirror.parent / "3-Wiki" / "Claude Code"
        assert (folder / "改过名.md").is_file()
        assert not (folder / "路由测试页.md").exists()

    @pytest.mark.django_db
    def test_the_description_endpoint_actually_routes_into_the_wiki_tree(
        self, isolate_markdown_mirror, session_client, wiki_page, workspace, create_user
    ):
        """走**真实端点**，证明三分支是接上的，不只是那个 helper 自己能动。

        上面几条直接调 `_mirror_wiki_page`（省掉整个请求周期、失败点精确）；
        这一条补上「调用点没被改坏」这半边 —— 协同服务器打的正是这个端点
        （见 `test_wiki_page_description_app.py` 的模块 docstring）。"""
        collection = PageCollection.objects.create(workspace=workspace, name="Claude Code", owned_by=create_user)
        Page.objects.filter(id=wiki_page.id).update(collection=collection)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{wiki_page.id}/description/",
            {"description_html": "<p>走端点的正文</p>", "description_json": {"type": "doc"}},
            format="json",
        )

        assert response.status_code == 200
        written = isolate_markdown_mirror.parent / "3-Wiki" / "Claude Code" / "路由测试页.md"
        assert written.is_file()
        assert written.read_text(encoding="utf-8").endswith("走端点的正文")
