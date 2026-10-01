# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""在 Wiki 里**新建**页面 —— ``POST workspaces/<slug>/wiki-pages/create/``。

这个端点是 Round B 的核心。在它之前，全仓**没有任何端点能建出「无项目」的页面**：
唯一的建页路由嵌在项目下（``urls/page.py`` 的
``workspaces/<slug>/projects/<uuid:project_id>/pages/``），而那条路上的序列化器
**从项目推 workspace**（``serializers/page.py:86-96``）—— 没有项目就没有 workspace。
而罗盘的 Wiki 需要页面可以在不属于任何项目的情况下存在。

两条不变量在这里锁住：

1. **无项目页建得出来，且不写 vault 镜像。** 镜像根 ``MARKDOWN_STORAGE_PATH``
   指向「项目」那一层（``…/ObsidianVault/2-项目``），所以无项目页**按定义无处可写** ——
   跳过 + 记日志，不是失败（Phase 1B 裁定，见 ``_wiki_page_project_id`` 的 docstring）。

2. **跨工作区守卫**：``project_id`` / ``collection_id`` 都必须属于路径上的那个工作区，
   否则 **404 且一页都不建**（校验排在写之前）。这条在 Task 2 落地。
"""

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Page, PageCollection, Project, ProjectPage, User, Workspace, WorkspaceMember


@pytest.fixture
def guest(db, workspace):
    """本工作区里的一个只读成员（GUEST, role=5）。

    ``plane/tests/conftest.py`` 里**没有** guest fixture，本仓库的契约测试模块各自
    定义自己需要的。形状照 ``test_wiki_page_description_app.py:100-118`` 抄 ——
    ``User.username`` 是 unique=True 且 ``create_user`` 已占用了 ""，所以必须给唯一值。
    """
    unique_id = uuid4().hex[:8]
    guest_user = User.objects.create(
        email=f"guest-{unique_id}@plane.so",
        username=f"guest_{unique_id}",
        first_name="Guest",
        last_name="User",
    )
    guest_user.set_password("test-password")
    guest_user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=guest_user, role=5)
    return guest_user


@pytest.fixture
def guest_client(guest):
    """以 GUEST 身份认证的客户端。

    **不要复用 ``session_client``** —— 它已经 ``force_authenticate(create_user)`` 过了。
    同一个 ``APIClient`` 实例上再认证一次是靠"后一次覆盖前一次"的副作用成立，
    读起来像在测 create_user 被拒。
    """
    client = APIClient()
    client.force_authenticate(user=guest)
    return client


@pytest.mark.contract
class TestWikiPageCreateWithoutAProject:
    """不带 ``project_id`` 也能建出一个页面 —— 本端点存在的唯一理由。"""

    @pytest.mark.django_db
    def test_creates_a_page_and_it_shows_up_in_general(self, session_client, workspace):
        response = session_client.post(f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert "id" in response.data, "前端靠响应里的 id 跳转到新页面"

        page = Page.objects.get(pk=response.data["id"])
        assert page.workspace_id == workspace.id
        # 在 Wiki 里建的页面天然已收录 —— is_global 就是收录标记。
        assert page.is_global is True
        assert page.access == Page.PUBLIC_ACCESS
        assert page.owned_by_id == response.wsgi_request.user.id
        # 关键的一行：没有项目。这正是本端点存在的理由。
        assert page.projects.count() == 0

        listed = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"collection": "general"})
        assert [str(row["id"]) for row in listed.data] == [str(page.id)]

        # 前端的落点是 `router.push('/wiki/<id>')`（`wiki/sidebar.tsx` 的新建流程），
        # 浏览器随即打**详情**路由。列表里查得到、详情取不到，用户看到的就是
        # 「建完跳过去 404」。详情与列表是**两套**过滤条件（`_wiki_page_queryset`
        # 走 `WikiPageViewSet.retrieve`），所以必须分开锁。
        detail = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{page.id}/")
        assert detail.status_code == status.HTTP_200_OK
        assert str(detail.data["id"]) == str(page.id)

    @pytest.mark.django_db
    def test_explicit_null_project_id_is_accepted(self, session_client, workspace):
        """显式传 ``project_id: null`` 也建得出来 —— 与"压根不传这个键"是两条输入。

        ``project_id`` 是 ``UUIDField(required=False, allow_null=True)``；少了
        ``allow_null``，一个显式的 null 会被 DRF 拒成 400，尽管它的语义就是"没有项目"。
        落库后走的是 ``is not None`` 的 **False** 分支：不建 ``ProjectPage`` 关联行
        （``WikiPageCreateSerializer.create`` 与 ``WikiPageViewSet.create_page``
        两处都以同一个 ``is not None`` 分叉，这条输入把它们一起走通了）。
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {"project_id": None}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        page = Page.objects.get(pk=response.data["id"])
        assert page.projects.count() == 0
        assert ProjectPage.objects.filter(page_id=page.id).count() == 0

    @pytest.mark.django_db
    def test_blank_name_is_accepted(self, session_client, workspace):
        """空名字合法 —— ``Page.name`` 是 ``TextField(blank=True)``，i18n 有
        ``wiki_collections.list.untitled``（「未命名」）而**没有**「页面名必填」键。
        官方把空名页当成一等公民。"""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {"name": ""}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(pk=response.data["id"]).name == ""

    @pytest.mark.django_db
    def test_whitespace_only_name_becomes_blank(self, session_client, workspace):
        """纯空白名必须**落库成空串**，不能原样存进去。

        前端发的是 `name.trim()`，所以正常情况下到不了这里；但 DRF 的
        `trim_whitespace=True`（默认）会先把 `" "` 切成 `""` 再校验，`allow_blank=True`
        才放行。少了任何一半，一个纯空白名要么 400、要么在侧栏渲染成一行看不见的东西。
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {"name": " "}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(pk=response.data["id"]).name == ""

    @pytest.mark.django_db
    def test_access_one_lands_in_the_private_partition(self, session_client, workspace):
        """``access=1`` ⇒ 落 ``private`` 分区。侧栏那个分区是**派生**的
        （``resolve_collection_key`` 的优先级是 archived > private > collection_id > general），
        所以"建到 Private 视图"在语义上只能是 ``access=1``。"""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {"access": 1}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        page_id = str(response.data["id"])

        private = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"collection": "private"})
        assert [str(row["id"]) for row in private.data] == [page_id]

        general = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"collection": "general"})
        assert general.data == []

        # 私有页同样要能被**详情**路由取到 —— 前端建完就跳 `/wiki/<id>`，
        # 而 `private` 是靠 `access=1` 派生的分区，详情那条 queryset 单独判一次。
        detail = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{page_id}/")
        assert detail.status_code == status.HTTP_200_OK
        assert str(detail.data["id"]) == str(page_id)

    @pytest.mark.django_db
    def test_rejects_an_access_value_that_is_not_zero_or_one(self, session_client, workspace):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {"access": 7}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Page.objects.count() == 0

    @pytest.mark.django_db
    def test_creates_into_a_named_collection(self, session_client, workspace, create_user):
        collection = PageCollection.objects.create(workspace=workspace, name="我的集合", owned_by=create_user)

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        page = Page.objects.get(pk=response.data["id"])
        assert page.collection_id == collection.id

        listed = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"collection": str(collection.id)})
        assert [str(row["id"]) for row in listed.data] == [str(page.id)]

        # 侧栏那个计数也是这个集合的 —— 建进去的页要算进 page_count。
        counts = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")
        # 左边要 `str()`：`PageCollectionSerializer` 继承 `BaseSerializer`，它的
        # `id` 是 `PrimaryKeyRelatedField`，`to_representation` 回的是 `value.pk` ——
        # UUID 主键就是**UUID 对象**（真实 HTTP 响应里由 JSONEncoder 渲染成字符串，
        # 但 `response.data` 是渲染前的 Python 对象）。同形断言见
        # `test_wiki_collections_app.py:167` 的 `str(...) == str(...)`。
        row = next(item for item in counts.data["collections"] if str(item["id"]) == str(collection.id))
        assert row["page_count"] == 1

    @pytest.mark.django_db
    def test_page_without_a_project_writes_no_mirror(self, session_client, workspace, isolate_markdown_mirror):
        """无项目 ⇒ 镜像根下**一个字节都不写**，且建页本身成功。

        ``rglob("*")`` 而不是 ``rglob("*.md")``：无守卫时 ``_project_name(None)``
        会退化成字符串 ``"None"``、``mkdir`` 出一个**空目录** —— ``*.md`` 看不见它，
        断言照样通过，这条测试就空转了（同 ``test_wiki_pages_app.py:1316-1319``）。
        """
        response = session_client.post(f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.count() == 1
        assert list(isolate_markdown_mirror.rglob("*")) == []

    @pytest.mark.django_db
    def test_guest_is_forbidden(self, guest_client, workspace):
        """写端点不含 GUEST —— 承 Round A 的收窄裁定。"""
        response = guest_client.post(f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Page.objects.count() == 0


@pytest.fixture
def project(workspace, create_user):
    """本工作区的一个项目 —— 可镜像的靶子。"""
    return Project.objects.create(name="镜像项目", identifier="MIR", workspace=workspace, created_by=create_user)


@pytest.fixture
def other_workspace(db, create_user):
    """另一个工作区。

    slug 必须与 ``workspace``（``conftest.py:158-162`` 固定为 ``"test-workspace"``）不同。

    **``create_user`` 同时是两个工作区的成员** —— 这是刻意的：``@allow_permission``
    只在 `workspace` 上放行，所以下面那两条 404 **只可能**来自本端点的归属守卫，
    不可能来自权限层。少了这一条，测试会通过而其实测的是别的东西。
    """
    other = Workspace.objects.create(name="别的工作区", owner=create_user, slug="other-workspace")
    WorkspaceMember.objects.create(workspace=other, member=create_user, role=20)
    return other


@pytest.fixture
def other_project(other_workspace, create_user):
    return Project.objects.create(
        name="别家的项目", identifier="OTH", workspace=other_workspace, created_by=create_user
    )


@pytest.fixture
def other_collection(other_workspace, create_user):
    return PageCollection.objects.create(workspace=other_workspace, name="别家的集合", owned_by=create_user)


@pytest.mark.contract
class TestWikiPageCreateWithAProject:
    """``project_id`` 可选。给了就挂到项目下，并**第一次真的写出 vault 镜像**。"""

    @pytest.mark.django_db
    def test_creates_a_page_with_a_project_and_mirrors_it(
        self, session_client, workspace, project, isolate_markdown_mirror
    ):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "有项目的页", "project_id": str(project.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        page = Page.objects.get(pk=response.data["id"])

        # 关联行照 serializers/page.py:99-105 的写法建的。
        assert ProjectPage.objects.filter(page_id=page.id).count() == 1
        assert page.projects.count() == 1

        # 镜像根指向「项目」那一层，所以有项目的页面**一定**有落点 ——
        # 与无项目页那条 skip 正好互为对照。
        mirrored = list(isolate_markdown_mirror.rglob("*.md"))
        assert len(mirrored) == 1, f"有项目的页面应当写出恰好一份镜像：{mirrored!r}"
        # 目录名是**项目名**，不是 id —— 这一行证明它落在了正确的那一层。
        assert mirrored[0].parent.name == "镜像项目"
        assert mirrored[0].name == "有项目的页.md"
        # frontmatter 里的 id 是页面的身份，改名搬移与去重都靠它。
        assert f"id: {page.id}" in mirrored[0].read_text(encoding="utf-8")

    @pytest.mark.django_db
    def test_rejects_a_project_from_another_workspace(
        self, session_client, workspace, other_project, isolate_markdown_mirror
    ):
        """别的工作区的项目 id → 404，且**一页都不建**。

        少了这个守卫，别家的项目 id 会被写进 ``ProjectPage``，而镜像路径是项目名派生的
        —— 等于把正文写到别的工作区的目录里。
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"project_id": str(other_project.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Page.objects.count() == 0
        assert ProjectPage.objects.count() == 0
        assert list(isolate_markdown_mirror.rglob("*")) == []

    @pytest.mark.django_db
    def test_rejects_a_collection_from_another_workspace(self, session_client, workspace, other_collection):
        """别的工作区的集合 id → 404，且**一页都不建**。

        与 ``WikiPageViewSet.create`` / ``partial_update`` 对 ``collection_id`` 的
        前置查同一条纪律、同一个状态码。守卫排在 ``save()`` 之前，所以 404 路径下
        一个字段都不会动。
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"collection_id": str(other_collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Page.objects.count() == 0


@pytest.fixture
def visible_parent(workspace, create_user):
    """本工作区里一个**已收录**的公开页面 —— 合法的父页。"""
    return Page.objects.create(
        name="父页",
        workspace=workspace,
        owned_by=create_user,
        is_global=True,
        description_html="<p></p>",
        description_json={},
    )


@pytest.fixture
def other_user(db):
    """第二个用户。用来造「别人的私有页」这个不可见的父页。"""
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"other-{unique_id}@plane.so",
        username=f"other_{unique_id}",
        first_name="Other",
        last_name="User",
    )
    user.set_password("test-password")
    user.save()
    return user


@pytest.mark.contract
class TestWikiPageCreateWithAParent:
    """子页面（设计 §3）—— ``parent`` 可选，且要过三条前置校验。

    三条缺一条都是洞：跨工作区（把子页挂到别家）、不可见（挂到别人的私有页底下）、
    未收录（子页挂在一个永远不会出现在树里的父页下面，变成看不见的孤儿）。
    """

    @pytest.mark.django_db
    def test_creates_a_child_under_a_parent(self, session_client, workspace, visible_parent):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "子页", "parent": str(visible_parent.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        child = Page.objects.get(pk=response.data["id"])
        assert child.parent_id == visible_parent.id
        # 子页照样是**已收录**的 —— 不然它不进树，等于白建。
        assert child.is_global is True

        listed = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"collection": "general"})
        assert {str(row["id"]) for row in listed.data} == {str(visible_parent.id), str(child.id)}
        # 列表响应必须带 `parent` —— 前端建树**只**靠它（Task 4 的 `pageParentIds`）。
        row = next(item for item in listed.data if str(item["id"]) == str(child.id))
        assert str(row["parent"]) == str(visible_parent.id)

    @pytest.mark.django_db
    def test_child_inherits_the_parents_collection(self, session_client, workspace, create_user, visible_parent):
        collection = PageCollection.objects.create(workspace=workspace, name="父页的集合", owned_by=create_user)
        visible_parent.collection = collection
        visible_parent.save(update_fields=["collection"])

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"parent": str(visible_parent.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(pk=response.data["id"]).collection_id == collection.id

    @pytest.mark.django_db
    def test_child_inherits_the_parents_private_access(self, session_client, workspace, visible_parent):
        """父页是私有的 ⇒ 子页也私有，落 private 分区。

        私有父页**自己的属主**当然看得见它（`_visible_page_q` 是
        `~Q(access=1) | Q(owned_by=user)`），所以这里不会撞上 B-2 的可见性校验。
        """
        visible_parent.access = Page.PRIVATE_ACCESS
        visible_parent.save(update_fields=["access"])

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"parent": str(visible_parent.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        child = Page.objects.get(pk=response.data["id"])
        assert child.access == Page.PRIVATE_ACCESS

        private = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"collection": "private"})
        assert {str(row["id"]) for row in private.data} == {str(visible_parent.id), str(child.id)}

    @pytest.mark.django_db
    def test_explicit_values_win_over_inheritance(self, session_client, workspace, visible_parent):
        """显式给了 `access` 就以显式为准 —— 继承是默认值，不是强制（设计 B-3）。"""
        visible_parent.access = Page.PRIVATE_ACCESS
        visible_parent.save(update_fields=["access"])

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"parent": str(visible_parent.id), "access": 0},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(pk=response.data["id"]).access == Page.PUBLIC_ACCESS

    @pytest.mark.django_db
    def test_explicit_null_collection_is_not_overridden(
        self, session_client, workspace, create_user, visible_parent
    ):
        """显式 `collection_id: null` 是「落 general」的表态，不该被父页的集合覆盖。

        判据必须是**原始请求体里有没有这个键**，不是 `validated_data` 里有没有值。
        """
        collection = PageCollection.objects.create(workspace=workspace, name="父页的集合", owned_by=create_user)
        visible_parent.collection = collection
        visible_parent.save(update_fields=["collection"])

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"parent": str(visible_parent.id), "collection_id": None},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(pk=response.data["id"]).collection_id is None

    @pytest.mark.django_db
    def test_rejects_a_parent_from_another_workspace(
        self, session_client, workspace, other_workspace, create_user
    ):
        """别的工作区的页面当父页 → 404，且**一页都不建**。"""
        foreign = Page.objects.create(
            name="别家的页",
            workspace=other_workspace,
            owned_by=create_user,
            is_global=True,
            description_html="<p></p>",
            description_json={},
        )

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"parent": str(foreign.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Page.objects.count() == 1  # 只有上面那个 foreign 页

    @pytest.mark.django_db
    def test_rejects_an_invisible_parent(self, session_client, workspace, other_user):
        """别人的私有页当父页 → 404。

        少了这条可见性校验，直接调 API 就能把子页挂到**别人的私有页**底下 ——
        与「私有页不进收录候选」是同一类纪律。
        """
        hidden = Page.objects.create(
            name="别人的私密页",
            workspace=workspace,
            owned_by=other_user,
            access=Page.PRIVATE_ACCESS,
            is_global=True,
            description_html="<p></p>",
            description_json={},
        )

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"parent": str(hidden.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Page.objects.count() == 1

    @pytest.mark.django_db
    def test_rejects_a_parent_that_is_not_included_in_the_wiki(self, session_client, workspace, create_user):
        """未收录（`is_global=False`）的页面当父页 → 404。

        树只列已收录页，所以挂在它下面的子页会是一个**永远看不见的孤儿**。
        界面上点不出来（`＋` 只长在树的行上），但 API 能调出来。
        """
        unlisted = Page.objects.create(
            name="未收录页",
            workspace=workspace,
            owned_by=create_user,
            is_global=False,
            description_html="<p></p>",
            description_json={},
        )

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"parent": str(unlisted.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Page.objects.count() == 1

    @pytest.mark.django_db
    def test_explicit_null_parent_is_accepted(self, session_client, workspace):
        """显式 `parent: null` 与「压根不传」是两条输入，都合法。"""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/", {"parent": None}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(pk=response.data["id"]).parent_id is None
