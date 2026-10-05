# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""换集合 ⇒ 镜像跟着走（复审后的修复 B）。

镜像路径的第一段是**集合名**（`3-Wiki/<集合>/…`），第二选择才是项目名
（`2-项目/<项目>/…`）。所以「换集合」和「改标题」一样会改变镜像路径 —— 但旧代码
只在改标题时才搬，而且拿 `save()` **之后**的集合去算旧路径：

1. 只换集合、不改名 → 闸门 `page.name != old_name` 为假，**一次都不搬**；
2. 改名 + 换集合 → 闸门为真，但旧路径落在**新**集合下（指向一个不存在的文件），
   `_move_page_file` 里 `if old_path.exists():` 为假 → 静默什么都不做。

两种结局一样：文件留在旧文件夹，下一次写正文在新集合下**再建一份**，
同一个 `frontmatter.id` 出现两份。这条缺陷从 UI 一击可达（`wiki-list-root.tsx`
的行菜单有 `move-to`）。

这里把「旧状态 × 新状态」全表锁住：

| 旧 → 新 | 期望 |
|---|---|
| 集合 A → 集合 B | 搬到 `3-Wiki/B/`，内容逐字不变 |
| 集合 A → 集合 B 且同时改名 | 只有一个文件、在新路径、旧路径无残骸 |
| 项目 → 集合（跨根） | `2-项目/…` → `3-Wiki/…` |
| 集合 → 项目（跨根） | 反向同上 |
| 集合 → 无家（无项目） | 搬进 `3-Wiki/常规/`，内容逐字不变（Round H 补的第三档，设计 §4.2） |
| 无家 → 任意 | 没有旧文件可搬；不报错，新文件由下一次写正文建立 |

`isolate_markdown_mirror` 把两棵镜像根都钉进 tmp_path：项目根 = `markdown-mirror`，
wiki 根 = `3-Wiki`（tmp_path 下的**兄弟**目录），所以「两棵树里一共有几份镜像」
用 `isolate_markdown_mirror.parent.rglob("*.md")` 数。
"""

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, Project, ProjectPage


def _wiki_page(workspace, user, name, **kwargs):
    """A public page included in the wiki — the shape the metadata route accepts."""
    return Page.objects.create(
        workspace=workspace,
        name=name,
        owned_by=user,
        access=Page.PUBLIC_ACCESS,
        is_global=True,
        description_html="<p></p>",
        description_json={},
        **kwargs,
    )


def _collection(workspace, user, name):
    return PageCollection.objects.create(workspace=workspace, name=name, owned_by=user)


def _project_with_page(workspace, project, page, user):
    """Link ``page`` to ``project`` via a live ``ProjectPage`` row."""
    return ProjectPage.objects.create(
        workspace=workspace,
        project=project,
        page=page,
        created_by_id=user.id,
        updated_by_id=user.id,
    )


@pytest.fixture
def project(workspace, create_user):
    return Project.objects.create(
        name="镜像项目",
        identifier="MIR",
        workspace=workspace,
        created_by=create_user,
    )


def _write_mirror(page_or_id, html="<p>原始正文</p>"):
    """Materialise a page's mirror through the same helper the writers use."""
    from plane.app.views.page.collection import _mirror_wiki_page

    page = Page.objects.get(id=getattr(page_or_id, "id", page_or_id))
    _mirror_wiki_page(page, html)


def _all_mirrors(isolate_markdown_mirror):
    """Every ``.md`` under **both** mirror roots (project tree + wiki tree)."""
    return sorted(isolate_markdown_mirror.parent.rglob("*.md"))


@pytest.mark.contract
class TestWikiMirrorCollectionMove:
    @pytest.mark.django_db
    def test_collection_change_moves_mirror_between_collection_folders(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """集合 A → 集合 B，名字不变：文件在新集合文件夹，旧文件夹下无残留，内容逐字不变。"""
        page = _wiki_page(workspace, create_user, "搬家页")
        a = _collection(workspace, create_user, "集合A")
        b = _collection(workspace, create_user, "集合B")
        Page.objects.filter(id=page.id).update(collection=a)

        _write_mirror(page)
        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        old_file = wiki_root / "集合A" / "搬家页.md"
        assert old_file.is_file(), "前置：镜像先落在旧集合文件夹里"
        before = old_file.read_text(encoding="utf-8")
        assert f"id: {page.id}" in before

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": str(b.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.collection_id == b.id

        new_file = wiki_root / "集合B" / "搬家页.md"
        assert new_file.is_file(), "镜像必须跟着集合搬到 3-Wiki/集合B/"
        assert not old_file.exists(), "旧集合文件夹下不得留残骸"
        assert new_file.read_text(encoding="utf-8") == before, "搬移不该改内容（含 frontmatter id）"

    @pytest.mark.django_db
    def test_rename_and_collection_change_moves_once(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """同时改名 + 换集合：只有一个文件、在新路径、旧路径不存在。

        这是今天会**静默留下旧文件**的那一档：闸门为真但旧路径按保存后的集合算，
        指向新集合下一个不存在的文件，`replace` 空转。
        """
        page = _wiki_page(workspace, create_user, "旧名字")
        a = _collection(workspace, create_user, "集合A")
        b = _collection(workspace, create_user, "集合B")
        Page.objects.filter(id=page.id).update(collection=a)

        _write_mirror(page)
        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        old_file = wiki_root / "集合A" / "旧名字.md"
        assert old_file.is_file()

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"name": "新名字", "collection_id": str(b.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.name == "新名字"
        assert page.collection_id == b.id

        assert not old_file.exists(), "旧路径不得留下残骸"
        mirrors = _all_mirrors(isolate_markdown_mirror)
        assert len(mirrors) == 1, f"改名 + 换集合后应当仍只有一份镜像：{mirrors!r}"
        assert mirrors[0] == wiki_root / "集合B" / "新名字.md"

    @pytest.mark.django_db
    def test_project_to_collection_moves_across_roots(
        self, session_client, isolate_markdown_mirror, workspace, create_user, project
    ):
        """跨根：`2-项目/<项目>/` → `3-Wiki/<集合>/`。"""
        page = _wiki_page(workspace, create_user, "跨根页")
        _project_with_page(workspace, project, page, create_user)
        collection = _collection(workspace, create_user, "目标集合")

        _write_mirror(page)
        project_file = isolate_markdown_mirror / "镜像项目" / "跨根页.md"
        assert project_file.is_file(), "前置：无集合时镜像落在项目树里"

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.collection_id == collection.id

        wiki_file = isolate_markdown_mirror.parent / "3-Wiki" / "目标集合" / "跨根页.md"
        assert wiki_file.is_file(), "跨根搬移必须把文件送进 wiki 树"
        assert not project_file.exists(), "项目树里不得留残骸"
        mirrors = _all_mirrors(isolate_markdown_mirror)
        assert len(mirrors) == 1, f"跨根搬移不得复制出第二份：{mirrors!r}"

    @pytest.mark.django_db
    def test_collection_to_project_moves_across_roots(
        self, session_client, isolate_markdown_mirror, workspace, create_user, project
    ):
        """反向跨根：`3-Wiki/<集合>/` → `2-项目/<项目>/`（collection_id: null）。"""
        page = _wiki_page(workspace, create_user, "回家的页")
        _project_with_page(workspace, project, page, create_user)
        collection = _collection(workspace, create_user, "原集合")
        Page.objects.filter(id=page.id).update(collection=collection)

        _write_mirror(page)
        wiki_file = isolate_markdown_mirror.parent / "3-Wiki" / "原集合" / "回家的页.md"
        assert wiki_file.is_file(), "前置：有集合时集合优先，镜像落在 wiki 树里"

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.collection_id is None

        project_file = isolate_markdown_mirror / "镜像项目" / "回家的页.md"
        assert project_file.is_file(), "移出集合后镜像必须落回项目树"
        assert not wiki_file.exists(), "wiki 树里不得留残骸"

    @pytest.mark.django_db
    def test_collection_to_homeless_page_moves_into_the_general_folder(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """集合 →「常规」且该页**没有**项目：文件搬进 `3-Wiki/常规/`，内容逐字不变。

        Round H 补上了「常规」这一档（设计 §4.2）：无集合、无项目的页面**不是**
        「无家可归」—— 它有 `3-Wiki/常规/` 可落。所以本档行为从「旧文件原地保留」
        变成「搬进常规」；§243「删集合不删文件夹」约束的是**删目录**，搬移不是删除：
        文件仍在（换了位置）、内容未动。
        """
        page = _wiki_page(workspace, create_user, "孤儿页")
        collection = _collection(workspace, create_user, "解散的集合")
        Page.objects.filter(id=page.id).update(collection=collection)

        _write_mirror(page)
        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        old_file = wiki_root / "解散的集合" / "孤儿页.md"
        assert old_file.is_file()
        before = old_file.read_text(encoding="utf-8")

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.collection_id is None

        new_file = wiki_root / "常规" / "孤儿页.md"
        assert new_file.is_file(), "无集合无项目 ⇒ 搬进 3-Wiki/常规/"
        assert new_file.read_text(encoding="utf-8") == before, "搬移不该改内容（含 frontmatter id）"
        assert not old_file.exists(), "旧集合文件夹下不得留残骸"
        assert _all_mirrors(isolate_markdown_mirror) == [new_file], "不得复制出第二份"

    @pytest.mark.django_db
    def test_homeless_page_arriving_at_a_collection_writes_nothing(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """无家 → 集合：没有旧文件可搬，什么都不做也不报错；新文件留给下一次写正文。"""
        page = _wiki_page(workspace, create_user, "从无到有")
        collection = _collection(workspace, create_user, "新集合")

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.collection_id == collection.id
        assert _all_mirrors(isolate_markdown_mirror) == [], "没有旧文件可搬时不该凭空造出一份"

    @pytest.mark.django_db
    def test_sub_page_folder_follows_collection_change(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """被搬页面**有子页**时，子页文件夹整体跟着走（`_move_page_file` 的 folder 逻辑）。"""
        parent = _wiki_page(workspace, create_user, "父页")
        child = _wiki_page(workspace, create_user, "子页", parent=parent)
        a = _collection(workspace, create_user, "集合A")
        b = _collection(workspace, create_user, "集合B")
        Page.objects.filter(id__in=[parent.id, child.id]).update(collection=a)

        # 先写父页自己的文件，再写子页 —— 子页会落进以父页命名的文件夹里。
        _write_mirror(parent)
        _write_mirror(child)
        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        assert (wiki_root / "集合A" / "父页.md").is_file()
        child_file = wiki_root / "集合A" / "父页" / "子页.md"
        assert child_file.is_file(), "前置：子页嵌在以父页命名的文件夹下"

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{parent.id}/",
            {"collection_id": str(b.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert (wiki_root / "集合B" / "父页.md").is_file()
        assert (wiki_root / "集合B" / "父页" / "子页.md").is_file(), "子页文件夹必须整体跟过去"
        assert not (wiki_root / "集合A" / "父页").exists(), "旧集合下不得留下空的/残的文件夹"

    @pytest.mark.django_db
    def test_description_endpoint_collection_change_moves_mirror(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """正文端点（`WikiPageDescriptionViewSet.partial_update`）是**第二个**调用点。

        协同服务器打的是这条路由，它也接受 `collection_id` —— 只改集合、不带正文时
        同样必须搬，只在元数据路由上修是不够的。
        """
        page = _wiki_page(workspace, create_user, "正文路由页")
        a = _collection(workspace, create_user, "集合A")
        b = _collection(workspace, create_user, "集合B")
        Page.objects.filter(id=page.id).update(collection=a)

        _write_mirror(page)
        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        old_file = wiki_root / "集合A" / "正文路由页.md"
        assert old_file.is_file()

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/description/",
            {"collection_id": str(b.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.collection_id == b.id
        assert (wiki_root / "集合B" / "正文路由页.md").is_file(), "正文路由同样要跟着集合搬镜像"
        assert not old_file.exists()

    @pytest.mark.django_db
    def test_one_request_that_moves_and_writes_the_body_lands_on_one_file(
        self, isolate_markdown_mirror, session_client, workspace, create_user
    ):
        """同一请求既换集合、又写正文 ⇒ 只留一份文件，正文落在被搬过去的那份上。

        正文端点（`WikiPageDescriptionViewSet.partial_update`）先搬后写，而写正文时
        `_wiki_page_own_path` 读的是**内存里**那个 `page.external_id`。只把新路径写进库、
        不写进实例的话，这一次写入仍会用旧 `own_path` 去比 ⇒ 目标文件不被认作自己的 ⇒
        冒出 `剪藏页-{id8}.md`。这条就是那行内存赋值的载荷测试。
        """
        source = _collection(workspace, create_user, "集合A")
        target = _collection(workspace, create_user, "集合B")
        page = _wiki_page(workspace, create_user, "剪藏页")
        Page.objects.filter(id=page.id).update(
            collection=source, external_source="obsidian-vault", external_id="3-Wiki/集合A/剪藏页.md"
        )

        # 导入形状的来源文件：**没有 `id:`**，所以归属只能靠 `own_path` 认。
        original = "---\ntags:\n  - 罗盘\n---\n\n用户手写的正文\n"
        source_file = isolate_markdown_mirror.parent / "3-Wiki" / "集合A" / "剪藏页.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(original, encoding="utf-8")

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/description/",
            {
                "collection_id": str(target.id),
                "description_html": "<p>Plane 正文</p>",
                "description_json": {"type": "doc"},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        moved = isolate_markdown_mirror.parent / "3-Wiki" / "集合B" / "剪藏页.md"
        assert moved.is_file(), "文件要搬到新集合下"
        assert moved.read_text(encoding="utf-8").endswith("Plane 正文"), "正文要落在这份文件上"
        assert list((isolate_markdown_mirror.parent / "3-Wiki" / "集合A").glob("*.md")) == [], "旧集合下不该留文件"
        assert not list(moved.parent.glob("剪藏页-*.md")), "不得冒出 -{id8} 兄弟文件"
        page.refresh_from_db()
        assert page.external_id == "3-Wiki/集合B/剪藏页.md"

    @pytest.mark.django_db
    def test_collection_change_repoints_an_imported_pages_external_id(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """导入形状的页换集合：`external_id` 跟着文件走，下一次写正文不另起兄弟文件。

        「导入页」的定义是本缺陷的关键：文件里**没有** `id:` 行（剪藏 / 手写
        frontmatter）。`_resolve_page_path` 对没有 `id:` 的文件唯一的判据就是
        「它是否**正好**是本页的 `own_path`」—— `own_path` 从 `external_id` 算。
        搬完不改 `external_id`，下一笔正文就把刚搬过去的那份当成别人的剪藏，
        改写 `X-<id8>.md` 兄弟文件，被搬走的那份从此陈旧。
        """
        page = _wiki_page(workspace, create_user, "剪藏页")
        a = _collection(workspace, create_user, "集合A")
        b = _collection(workspace, create_user, "集合B")
        Page.objects.filter(id=page.id).update(
            collection=a, external_source="obsidian-vault", external_id="3-Wiki/集合A/剪藏页.md"
        )

        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        old_file = wiki_root / "集合A" / "剪藏页.md"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        original = "---\ntags: [剪藏]\n---\n\n从网页剪下来的正文\n"
        old_file.write_text(original, encoding="utf-8")

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": str(b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        page.refresh_from_db()
        assert page.collection_id == b.id
        assert page.external_id == "3-Wiki/集合B/剪藏页.md", "行指针必须跟着文件走"

        new_file = wiki_root / "集合B" / "剪藏页.md"
        assert new_file.is_file(), "镜像必须搬到新集合文件夹"
        assert new_file.read_text(encoding="utf-8") == original, "搬移不得动内容（含剪藏的 frontmatter 键）"
        assert not old_file.exists(), "旧集合文件夹下不得留残骸"

        # 再走一次**正文**保存：如果 `external_id` 没跟，这一笔会在新集合下写出
        # `剪藏页-<id8>.md` 兄弟文件，刚搬过去的那份反而不动了。
        body = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/description/",
            {"description_html": "<p>搬家后的新正文</p>", "description_json": {"type": "doc"}},
            format="json",
        )
        assert body.status_code == status.HTTP_200_OK
        sibling = wiki_root / "集合B" / f"剪藏页-{str(page.id)[:8]}.md"
        assert not sibling.exists(), "指针跟对了就不会再冒出一个兄弟文件"
        assert new_file.is_file(), "正文必须落回被搬过来的那份文件"
        assert _all_mirrors(isolate_markdown_mirror) == [new_file], "正文保存后仍应只有一份镜像"

    @pytest.mark.django_db
    def test_rename_repoints_an_imported_pages_external_id(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """同一收口的另一支：只改名（集合不动），`external_id` 同样得跟着走。

        改名与换集合搬的是同一个收口 `_move_wiki_page_mirror`，判据也同一条：
        文件搬到哪，指针跟到哪。少了这一跟，改名后的第一笔正文同样落在兄弟文件上。
        """
        page = _wiki_page(workspace, create_user, "旧剪藏名")
        a = _collection(workspace, create_user, "集合A")
        Page.objects.filter(id=page.id).update(
            collection=a, external_source="obsidian-vault", external_id="3-Wiki/集合A/旧剪藏名.md"
        )

        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        old_file = wiki_root / "集合A" / "旧剪藏名.md"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        original = "---\ntags: [改名前]\n---\n\n正文\n"
        old_file.write_text(original, encoding="utf-8")

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"name": "新剪藏名"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        page.refresh_from_db()
        assert page.name == "新剪藏名"
        assert page.external_id == "3-Wiki/集合A/新剪藏名.md", "行指针必须跟着改名后的文件走"

        new_file = wiki_root / "集合A" / "新剪藏名.md"
        assert new_file.is_file()
        assert new_file.read_text(encoding="utf-8") == original, "搬移不得动内容"
        assert not old_file.exists(), "旧名字下不得留残骸"

        body = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/description/",
            {"description_html": "<p>改名后的新正文</p>", "description_json": {"type": "doc"}},
            format="json",
        )
        assert body.status_code == status.HTTP_200_OK
        sibling = wiki_root / "集合A" / f"新剪藏名-{str(page.id)[:8]}.md"
        assert not sibling.exists(), "指针跟对了就不会再冒出一个兄弟文件"
        assert new_file.is_file(), "正文必须落回改名后的那份文件"
        assert _all_mirrors(isolate_markdown_mirror) == [new_file], "正文保存后仍应只有一份镜像"

    @pytest.mark.django_db
    def test_cross_root_move_leaves_external_id_alone(
        self, session_client, isolate_markdown_mirror, workspace, create_user, project
    ):
        """护栏：集合 → 项目（跨根）时 `external_id` **一字不得改**。

        `external_id`-as-locator 是 **wiki 树**内的概念（`3-Wiki/…`）。跨根搬移之后
        文件在 `2-项目/…`，那个位置不由 wiki 根拼出来，指针跟过去就指向了一个
        本函数管不着的地方 —— 守住「只在 wiki 根内才改指针」。
        """
        page = _wiki_page(workspace, create_user, "跨根剪藏")
        _project_with_page(workspace, project, page, create_user)
        a = _collection(workspace, create_user, "集合A")
        Page.objects.filter(id=page.id).update(
            collection=a, external_source="obsidian-vault", external_id="3-Wiki/集合A/跨根剪藏.md"
        )

        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        old_file = wiki_root / "集合A" / "跨根剪藏.md"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_text("---\ntags: [跨根]\n---\n\n正文\n", encoding="utf-8")

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": None},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        page.refresh_from_db()
        assert page.collection_id is None
        assert page.external_id == "3-Wiki/集合A/跨根剪藏.md", "跨根搬移不归 wiki 指针管，一字不得改"

        project_file = isolate_markdown_mirror / "镜像项目" / "跨根剪藏.md"
        assert project_file.is_file(), "文件本身仍要照搬"
        assert not old_file.exists(), "wiki 树里不得留残骸"

    @pytest.mark.django_db
    def test_page_without_an_external_id_gains_none(
        self, session_client, isolate_markdown_mirror, workspace, create_user
    ):
        """护栏：Plane 原生页（没有 `external_id`）换集合后**仍然没有** —— 不无中生有。

        给原生页造一个来源凭证，等于让它此后每一笔正文写入都把那个位置上
        恰好同名的用户笔记认成自己的。搬移照旧，指针保持空。
        """
        page = _wiki_page(workspace, create_user, "原生页")
        a = _collection(workspace, create_user, "集合A")
        b = _collection(workspace, create_user, "集合B")
        Page.objects.filter(id=page.id).update(collection=a)
        assert page.external_id is None, "前置：这是一张 Plane 原生页"

        _write_mirror(page)
        wiki_root = isolate_markdown_mirror.parent / "3-Wiki"
        old_file = wiki_root / "集合A" / "原生页.md"
        assert old_file.is_file()

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/",
            {"collection_id": str(b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        page.refresh_from_db()
        assert page.collection_id == b.id
        assert page.external_id is None, "不得给 Plane 原生页凭空造一个来源凭证"
        assert (wiki_root / "集合B" / "原生页.md").is_file(), "搬移本身照旧"
        assert not old_file.exists()
