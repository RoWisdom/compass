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
    def test_a_folder_writes_no_vault_mirror(self, session_client, workspace, isolate_markdown_mirror):
        """**这是本 Task 的核心不变量**（设计 §5.2）。

        镜像根指向 `MARKDOWN_STORAGE_PATH`（"项目"那一层）。对一个没有正文的节点跑一遍
        markdown 落盘，会在 vault 里凭空生出一个 `<文件夹名>.md` 空文件 —— 那是往用户的
        真实笔记库里写垃圾。
        """
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/wiki-pages/create/",
            {"name": "不该落盘的文件夹", "node_type": "folder"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert sorted(isolate_markdown_mirror.parent.rglob("*.md")) == []
