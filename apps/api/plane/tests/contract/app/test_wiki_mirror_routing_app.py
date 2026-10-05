# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""wiki 页面的镜像是**三分支**的（设计 §3.3；第三档由 Round H 落成目录）：

| 页面 | 落点 |
|---|---|
| 有集合 | `3-Wiki/<集合名>/…` |
| 无集合、有项目 | `2-项目/<项目名>/…`（逐字不变，这里有回归锁） |
| 都没有 | `3-Wiki/常规/…`（Round H 之前是「不落盘 + warning」） |

前两行的**顺序**是 Phase 1B 的裁定：集合优先于项目。第三行是 Round H 补的空格 ——
补之前「删集合」会把 226 上全部 35 个无项目链接的页面踢出镜像同步。
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
    def test_page_with_neither_now_mirrors_into_the_general_folder(
        self, isolate_markdown_mirror, wiki_page, workspace, create_user
    ):
        """Round H 起第三档不再是「不落盘」：无集合、无项目的页面落 `3-Wiki/常规/`。

        （旧行为由 Phase 1B 的 §2.3c 裁定 —— 那时没有「常规」这个目录可落。
        226 实测 35 个 wiki 页面**全部**没有项目链接，不补这一档，删集合会把它们
        永久踢出镜像同步。）
        """
        from plane.app.views.page.collection import _mirror_wiki_page

        _mirror_wiki_page(Page.objects.get(id=wiki_page.id), "<p>新正文</p>")

        written = isolate_markdown_mirror.parent / "3-Wiki" / "常规" / "路由测试页.md"
        assert written.is_file()
        assert written.read_text(encoding="utf-8").endswith("新正文")
        assert not isolate_markdown_mirror.exists(), "不得落进项目树"

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
