# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Wiki 里的**文件夹**（罗盘 Round D）—— ``Page.node_type`` 判别符。

路线 2（设计 §3）：**不建第二张表**，复用 `Page.parent` 这棵自引用树，用一个
`node_type` 字段区分节点种类。行为照 Confluence Cloud 的 Folders 模型（设计 §2
的 F1–F17）：文件夹是一等内容类型、没有正文、不进任何计数、可任意层级嵌套、
类型建时定死且**只读**（F16：文件夹不能变回页面）。

本文件按 Task 分批长出来：
  · Task 2  字段存在 + 两条读路径（树 / 详情）露字段
  · Task 3  建页路径收 `node_type`、文件夹不写 vault 镜像
  · Task 4  正文端点对文件夹的守卫（改名/换集合仍放行）
  · Task 5  集合计数排除文件夹
  · Task 6  ``?folder=<uuid>`` 子树分支
"""

from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import Page, PageCollection, Project, User
from plane.utils.error_codes import ERROR_CODES


def _wiki_page(workspace, user, name, **kwargs):
    """一行已收录进 Wiki 的页面。默认是最普通的 general 公开页。"""
    defaults = {
        "name": name,
        "workspace": workspace,
        "owned_by": user,
        "is_global": True,
        "description_html": "<p></p>",
        "description_json": {},
    }
    defaults.update(kwargs)
    return Page.objects.create(**defaults)


def _folder(workspace, user, name, **kwargs):
    """一行文件夹。**与 `_wiki_page` 的唯一差别就是 `node_type`** —— 这正是路线 2 的重点。"""
    kwargs.setdefault("node_type", Page.NODE_TYPE_FOLDER)
    return _wiki_page(workspace, user, name, **kwargs)


@pytest.fixture
def project(workspace, create_user):
    """一个本工作区的项目。形状照 ``test_wiki_mirror_collection_move_app.py:66-73`` 抄 ——
    契约测试模块各自定义自己需要的 fixture，`plane/tests/conftest.py` 里没有 `project`。"""
    return Project.objects.create(
        name="文件夹测试项目",
        identifier="FLD",
        workspace=workspace,
        created_by=create_user,
    )


@pytest.fixture
def collection(workspace, create_user):
    """一个本工作区自建的集合。形状照 ``test_wiki_mirror_collection_move_app.py:56`` 抄 ——
    契约测试模块各自定义自己需要的 fixture，`plane/tests/conftest.py` 里没有 `collection`。"""
    return PageCollection.objects.create(workspace=workspace, name="不该落盘集合", owned_by=create_user)


@pytest.fixture
def folder_tree(workspace, create_user):
    """三层嵌套，覆盖后面每个 Task 需要的形状：

        A（文件夹）
        ├── B（文件夹）
        │   ├── t1（页面）
        │   └── C（文件夹）
        │       └── t2（页面）
        ├── t3（页面）
        └── (空文件夹 D)
        outside（页面，A 完全无关）

    `create_user` 是 `session_client` 认证的那个人 —— 全树对他可见。
    """
    a = _folder(workspace, create_user, "A")
    b = _folder(workspace, create_user, "B", parent=a)
    c = _folder(workspace, create_user, "C", parent=b)
    t1 = _wiki_page(workspace, create_user, "t1", parent=b)
    t2 = _wiki_page(workspace, create_user, "t2", parent=c)
    t3 = _wiki_page(workspace, create_user, "t3", parent=a)
    d = _folder(workspace, create_user, "D", parent=a)
    outside = _wiki_page(workspace, create_user, "outside")
    return {"a": a, "b": b, "c": c, "d": d, "t1": t1, "t2": t2, "t3": t3, "outside": outside}


@pytest.mark.contract
class TestNodeTypeFieldExists:
    @pytest.mark.django_db
    def test_the_three_constants_have_the_designed_values(self):
        """值本身是契约 —— 迁移里写死了 `"doc"` / `"folder"`，改常量不改迁移会静默分叉。"""
        assert Page.NODE_TYPE_DOC == "doc"
        assert Page.NODE_TYPE_FOLDER == "folder"
        assert Page.NODE_TYPE_CHOICES == (("doc", "Document"), ("folder", "Folder"))

    @pytest.mark.django_db
    def test_a_plain_page_defaults_to_doc(self, workspace, create_user):
        """**默认值是"doc"** —— 这是本轮"既有页面行为逐字不变"的全部依据。

        不是断言 `_meta.get_field(...).default`（那样只测了声明），而是**建一行真的读回来**：
        迁移给存量行填的值与模型给新行的默认值，在这里被同一个断言钉住。
        """
        page = Page.objects.create(
            name="普通页",
            workspace=workspace,
            owned_by=create_user,
            description_html="<p></p>",
            description_json={},
        )
        assert Page.objects.get(pk=page.id).node_type == Page.NODE_TYPE_DOC


@pytest.mark.contract
class TestNodeTypeOnReadPaths:
    """类型必须**从服务端来** —— 前端不推导（设计 §5.3：前端只读，不发明）。"""

    @pytest.mark.django_db
    def test_the_scope_all_tree_carries_node_type(self, session_client, workspace, folder_tree):
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"scope": "all"})

        assert response.status_code == status.HTTP_200_OK
        by_id = {str(row["id"]): row for row in response.data}
        for key in ("a", "b", "c", "d"):
            assert by_id[str(folder_tree[key].id)]["node_type"] == Page.NODE_TYPE_FOLDER
        for key in ("t1", "t2", "t3", "outside"):
            assert by_id[str(folder_tree[key].id)]["node_type"] == Page.NODE_TYPE_DOC

    @pytest.mark.django_db
    def test_the_default_list_path_does_not_carry_node_type(self, session_client, workspace, folder_tree):
        """**硬要求**（裁定 6）：不带 `scope` / 不带 `folder` 的那条路径逐字不变。

        `WikiPageSerializer` 有没有多出 `node_type`，就看这一条 —— 多出来的键会顺着
        前端 `mutateProperties` 被写成没人认识的属性。
        """
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/", {"collection": "general"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) > 0
        for row in response.data:
            assert "node_type" not in row

    @pytest.mark.django_db
    def test_the_detail_endpoint_carries_node_type(self, session_client, workspace, folder_tree):
        response = session_client.get(f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['a'].id}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["node_type"] == Page.NODE_TYPE_FOLDER


@pytest.mark.contract
class TestCreatingAFolder:
    """``POST wiki-pages/create/`` 的 ``node_type``（设计 §5.2）。

    设计 §5.3 的界面形状照 Confluence F7：**新建文件夹不是一个独立端点** ——
    它就是同一个建页请求多带一个字段。这里锁的就是"同一个端点、多一个字段"。
    """

    @pytest.mark.django_db
    def test_creates_a_folder_when_node_type_is_folder(self, session_client, workspace):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "我的文件夹", "node_type": "folder"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        folder = Page.objects.get(pk=response.data["id"])
        assert folder.node_type == Page.NODE_TYPE_FOLDER
        # 文件夹照样是**已收录**的 —— 不然它不进树，等于白建。
        assert folder.is_global is True
        # 正文列留默认值。**不去动它是刻意的**：给文件夹一个"空正文"和给它一个
        # `None` 在库里有区别，而详情端点对两者都读得出来；保持一致更省事。
        assert folder.description_html == "<p></p>"

    @pytest.mark.django_db
    def test_defaults_to_doc_when_node_type_is_absent(self, session_client, workspace):
        """**缺键 ⇒ doc**。这是"既有调用方一行不改"的全部依据 —— 前端今天发的正是这条请求。"""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "普通页"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(pk=response.data["id"]).node_type == Page.NODE_TYPE_DOC

    @pytest.mark.django_db
    def test_rejects_an_unknown_node_type(self, session_client, workspace):
        """`ChoiceField` 而不是 `CharField` + 手写范围检查 —— 与 `access` 同一条纪律：
        `Page.NODE_TYPE_CHOICES` 就是模型自己声明的合法集，别在序列化器里存第二份真相。"""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "啥", "node_type": "not-a-type"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "node_type" in response.data
        assert not Page.objects.filter(name="啥").exists(), "校验失败时一行都不该建出来"

    @pytest.mark.django_db
    def test_a_folder_cannot_belong_to_a_project(self, session_client, workspace, project):
        """裁定 8：文件夹没有正文 ⇒ 不写镜像 ⇒ 「有没有项目」唯一的作用消失。

        静默丢弃 `project_id` 比报错更坏 —— 调用方会以为挂上了。
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "有项目的文件夹", "node_type": "folder", "project_id": str(project.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "project_id" in response.data
        assert not Page.objects.filter(name="有项目的文件夹").exists()

    @pytest.mark.django_db
    def test_a_folder_is_created_under_a_parent_folder(self, session_client, workspace, folder_tree):
        """F5：任意层级嵌套 —— 文件夹的父可以也是文件夹。"""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "第四层", "node_type": "folder", "parent": str(folder_tree["c"].id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        created = Page.objects.get(pk=response.data["id"])
        assert created.parent_id == folder_tree["c"].id
        assert created.node_type == Page.NODE_TYPE_FOLDER

    @pytest.mark.django_db
    def test_a_folder_writes_no_vault_mirror(self, session_client, workspace, collection, isolate_markdown_mirror):
        """**这是本 Task 的核心不变量**（设计 §5.2）。

        镜像根指向 `MARKDOWN_STORAGE_PATH`（"项目"那一层）。对一个没有正文的节点跑一遍
        markdown 落盘，会在 vault 里凭空生出一个 `<文件夹名>.md` 空文件 —— 那是往用户的
        真实笔记库里写垃圾。

        **`collection_id` 不是装饰，是这条测试的鉴别力所在**：`_mirror_wiki_page` 是
        **集合优先**的（`collection_id is not None` 就先走集合分支并 return）。不带集合时，
        无集合无项目的页会在"没有落脚点"那一步就 warn 返回 —— **与 `node_type` 无关** ——
        于是把文件夹的跳过整个删掉，这条断言照样通过。带上集合之后，少了文件夹跳过就
        **真的**会落一个 `<文件夹名>.md` 下来。
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "不该落盘的文件夹", "node_type": "folder", "collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert sorted(isolate_markdown_mirror.parent.rglob("*.md")) == []


@pytest.mark.contract
class TestFolderBodyGuard:
    """文件夹没有正文（F2 / F4）—— 往它身上写正文必须被拒。

    **只拦正文三个键**（裁定 3）：改名与换集合走同一个端点（`WikiPageUpdateSerializer`
    的字段），而侧栏的文件夹行靠改名、`move-to-collection` 靠换集合 —— 一刀切成 400
    会把这两条既有能力一起砍掉。

    **三个键都拦**：协同编辑器 PATCH 的是 `description_binary`（Yjs 全量二进制），
    它和 `description_html` / `description_json` 一样是正文。设计 §5.2 只点了后两个，
    漏了它（见裁定 3）。
    """

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "payload",
        [
            {"description_html": "<p>偷写正文</p>"},
            {"description_json": {"type": "doc", "content": []}},
            {"description_binary": "AAECAwQ="},
        ],
    )
    def test_rejects_every_body_key(self, session_client, workspace, folder_tree, payload):
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['a'].id}/description/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ERROR_CODES["PAGE_IS_FOLDER"]
        assert response.data["error_message"] == "PAGE_IS_FOLDER"
        assert Page.objects.get(pk=folder_tree["a"].id).description_html == "<p></p>", "被拒时一个字段都不该动"

    @pytest.mark.django_db
    def test_the_error_code_is_4703(self):
        """错误码本身是契约 —— 前端按它分支（`ERROR_CODES` 全仓只有一份）。"""
        assert ERROR_CODES["PAGE_IS_FOLDER"] == 4703

    @pytest.mark.django_db
    def test_a_folder_can_still_be_renamed(self, session_client, workspace, folder_tree):
        """侧栏的文件夹行靠改名（F7 的 `⋯` 里那一项）。"""
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['a'].id}/description/",
            {"name": "A 改名后"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert Page.objects.get(pk=folder_tree["a"].id).name == "A 改名后"

    @pytest.mark.django_db
    def test_a_folder_can_still_move_to_another_collection(
        self, session_client, workspace, create_user, folder_tree
    ):
        """`move-to-collection` 走的是同一个端点、换的是 `collection_id` —— 必须放行。"""
        collection = PageCollection.objects.create(workspace=workspace, name="新家", owned_by=create_user)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['a'].id}/description/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert Page.objects.get(pk=folder_tree["a"].id).collection_id == collection.id

    @pytest.mark.django_db
    def test_an_ordinary_page_still_accepts_a_body(self, session_client, workspace, folder_tree):
        """**反向断言**：守卫不能误伤页面 —— 漏了它，一个把 `node_type` 判反的实现也会全绿。"""
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['t1'].id}/description/",
            {"description_html": "<p>正常正文</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert Page.objects.get(pk=folder_tree["t1"].id).description_html == "<p>正常正文</p>"

    # 以上是**正文端点**（`…/description/`）。以下是**兄弟端点** —— **元数据路由**
    # （`PATCH …/wiki-pages/<id>/`）。它同样接受 `WikiPageUpdateSerializer` 的正文三个键，
    # 因此同一条不变量（文件夹没有正文）必须在这里再守一次；只守正文端点，
    # 直连本端点就能给文件夹塞进一段正文。

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "payload",
        [
            {"description_html": "<p>偷写正文</p>"},
            {"description_json": {"type": "doc", "content": []}},
            {"description_binary": "AAECAwQ="},
        ],
    )
    def test_the_metadata_route_rejects_every_body_key(self, session_client, workspace, folder_tree, payload):
        """元数据路由与正文端点同一条不变量、同一个错误码、同一个信封。"""
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['a'].id}/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ERROR_CODES["PAGE_IS_FOLDER"]
        assert response.data["error_message"] == "PAGE_IS_FOLDER"
        assert Page.objects.get(pk=folder_tree["a"].id).description_html == "<p></p>", "被拒时一个字段都不该动"

    @pytest.mark.django_db
    def test_a_folder_can_still_be_renamed_via_the_metadata_route(self, session_client, workspace, folder_tree):
        """标题防抖同步造访的正是这条路径（`base-page.ts` 只发 `{ name }`）—— 不许误伤。"""
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['a'].id}/",
            {"name": "A 改名后"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert Page.objects.get(pk=folder_tree["a"].id).name == "A 改名后"

    @pytest.mark.django_db
    def test_a_folder_can_still_move_to_another_collection_via_the_metadata_route(
        self, session_client, workspace, collection, folder_tree
    ):
        """换集合走 `{ collection_id }`（`workspace-page.store.ts`）—— 必须放行。"""
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['a'].id}/",
            {"collection_id": str(collection.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert Page.objects.get(pk=folder_tree["a"].id).collection_id == collection.id

    @pytest.mark.django_db
    def test_an_ordinary_page_still_accepts_a_body_via_the_metadata_route(self, session_client, workspace, folder_tree):
        """**反向断言**：漏了它，一个把 `node_type` 判反的实现也会全绿。"""
        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/wiki-pages/{folder_tree['t1'].id}/",
            {"description_html": "<p>正常正文</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert Page.objects.get(pk=folder_tree["t1"].id).description_html == "<p>正常正文</p>"


@pytest.mark.contract
class TestFolderCountsAsItsOwnKind:
    """文件夹**不进任何计数**（Confluence F14：它是一类，不是页面）。

    不做的话：根级文件夹会被算进「常规」的 `page_count` —— 侧栏显示「常规 (7)」
    而点进去只有 6 行页面，正是本仓明确讨厌过的那类口径分裂（设计 §3.4）。

    **注意这与 `test_wiki_pages_scope_app.py::test_partition_sizes_match_the_collection_counts`
    不是同一条口径了**：「树里每分区的条数 == 该分区的计数」在本轮之后**有意**不再成立 ——
    树列的是**节点**（含文件夹），计数算的是**页面**。那条既有测试用的 `pages` fixture
    里一个文件夹都没有，所以它仍然绿；**别**为了"让两条口径一致"去改它。
    """

    @pytest.mark.django_db
    def test_a_root_folder_is_not_counted_in_general(self, session_client, workspace, create_user):
        _wiki_page(workspace, create_user, "唯一的页面")
        _folder(workspace, create_user, "不该被算的文件夹")

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        assert response.status_code == status.HTTP_200_OK
        general = next(row for row in response.data["predefined"] if row["key"] == "general")
        assert general["page_count"] == 1

    @pytest.mark.django_db
    def test_a_folder_in_a_custom_collection_is_not_counted(self, session_client, workspace, create_user):
        """自建集合那条分支**两样东西**都要对：页面算 1、文件夹不算。

        只断言文件夹不算是不够的 —— 一个把整个循环 `continue` 掉的实现也会让它绿。
        """
        collection = PageCollection.objects.create(workspace=workspace, name="我的集合", owned_by=create_user)
        _wiki_page(workspace, create_user, "集合里的页面", collection=collection)
        _folder(workspace, create_user, "集合里的文件夹", collection=collection)

        response = session_client.get(f"/api/workspaces/{workspace.slug}/page-collections/")

        row = next(item for item in response.data["collections"] if item["name"] == "我的集合")
        assert row["page_count"] == 1
